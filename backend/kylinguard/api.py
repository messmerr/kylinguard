"""FastAPI 入口：SSE 任务流、人工确认、审计查询与前端托管。

应用按本机单用户运维工具运行，业务端点不要求登录；人工确认统一以本机
操作者身份写入审计链。SSE 断开时任务安全取消并补写可回放终态。
"""
import asyncio
import getpass
import json
import logging
import os
import pwd
import shutil
import sqlite3
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator

from kylinguard.audit import AuditError, AuditLog
from kylinguard.authorization import execution_profile_fingerprint
from kylinguard.config import Settings, get_settings
from kylinguard.llm import (
    LLMError,
    internal_error,
    public_error,
    public_error_from_exception,
)
from kylinguard.llm_config import (
    LLMConfigError,
    LLMConfigStore,
    LLMConfigVersionConflict,
    LLMRuntime,
    ModelSelection,
)
from kylinguard.mcp_client import ToolManager
from kylinguard.models import (
    PermissionDecision,
    PermissionGrantScope,
    PermissionMode,
    PermissionResolution,
)
from kylinguard.permissions import (
    PermissionError as SessionPermissionError,
    PermissionRequests,
    PermissionVersionConflict,
    expires_after,
    normalize_trusted_root,
)
from kylinguard.pipeline import Confirmations, Pipeline
from kylinguard.planner import Planner
from kylinguard.policy import KINDS, PolicyStore
from kylinguard.reviewer import Reviewer
from kylinguard.rules import builtin_rules
from kylinguard.alert_rules import AlertRuleStore
from kylinguard.alert_pusher import push_channel
from kylinguard.sessions import SessionStore
from kylinguard.snapshot import SnapshotCache
from kylinguard.storage_security import secure_database_path
from kylinguard.subprocess_env import safe_subprocess_env

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_LOCAL_OPERATOR = "local"
logger = logging.getLogger(__name__)

ReasoningEffort = Literal[
    "auto", "none", "minimal", "low", "medium", "high", "xhigh", "max",
]
ProviderAdapter = Literal[
    "openai", "deepseek", "dashscope", "openai_compatible",
]


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""  # 空 = 新建会话（SSE 首事件返回 session_created）
    request_id: str = Field(
        default="", max_length=128,
        pattern=r"^[A-Za-z0-9._:-]*$",
    )
    permission_mode: PermissionMode | None = None
    trusted_roots: list[str] = Field(default_factory=list, max_length=32)
    permission_ttl_seconds: int | None = Field(default=None, ge=1)
    workspace_root: str = Field(default="", max_length=4096)
    provider_id: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=256)
    reasoning_effort: ReasoningEffort = "auto"

    @model_validator(mode="after")
    def validate_initial_permissions(self):
        if bool(self.provider_id) != bool(self.model_id):
            raise ValueError("provider_id 与 model_id 必须同时提供")
        if not self.provider_id and self.reasoning_effort != "auto":
            raise ValueError("指定推理强度时必须同时指定模型")
        if self.permission_mode is None:
            if self.trusted_roots or self.permission_ttl_seconds is not None:
                raise ValueError("设置可信目录或有效期时必须同时指定 permission_mode")
            return self
        if self.permission_mode == PermissionMode.FULL_ACCESS:
            raise ValueError("完全访问必须在会话创建后通过独立权限接口启用")
        if self.permission_mode == PermissionMode.TRUSTED_WORKSPACE:
            if not self.trusted_roots:
                raise ValueError("信任目录模式至少需要一个可信目录")
        elif self.trusted_roots or self.permission_ttl_seconds is not None:
            raise ValueError("当前权限模式不能设置可信目录或有效期")
        return self


class ConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class SessionCreateRequest(BaseModel):
    """在首条消息前原子创建完全访问草稿会话。"""

    session_id: str = Field(
        min_length=32,
        max_length=32,
        pattern=r"^[a-f0-9]{32}$",
    )
    mode: PermissionMode
    ttl_seconds: int = Field(ge=1)
    workspace_root: str = Field(default="", max_length=4096)
    provider_id: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=256)
    reasoning_effort: ReasoningEffort = "auto"

    @model_validator(mode="after")
    def require_full_access(self):
        if self.mode != PermissionMode.FULL_ACCESS:
            raise ValueError("预创建会话接口仅接受 full_access 模式")
        if bool(self.provider_id) != bool(self.model_id):
            raise ValueError("provider_id 与 model_id 必须同时提供")
        if not self.provider_id and self.reasoning_effort != "auto":
            raise ValueError("指定推理强度时必须同时指定模型")
        return self


class LLMModelRequest(BaseModel):
    id: str = Field(min_length=1, max_length=256)
    label: str = Field(default="", max_length=256)
    enabled: bool = True
    supported_efforts: list[ReasoningEffort] = Field(
        default_factory=list, max_length=8,
    )
    supports_temperature: bool = False

    @field_validator("id", "label")
    @classmethod
    def clean_model_text(cls, value: str) -> str:
        value = value.strip()
        if any(ord(char) < 32 for char in value):
            raise ValueError("模型名称不能包含控制字符")
        return value

    @field_validator("supported_efforts")
    @classmethod
    def unique_efforts(cls, value: list[str]) -> list[str]:
        if "auto" in value:
            raise ValueError("auto 是界面回退选项，不写入模型能力列表")
        return list(dict.fromkeys(value))


class LLMProviderRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    adapter: ProviderAdapter
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    clear_api_key: bool = False
    models: list[LLMModelRequest] = Field(default_factory=list, max_length=256)
    enabled: bool = True
    allow_insecure_http: bool = False
    version: int | None = Field(default=None, ge=1)

    @field_validator("name", "base_url")
    @classmethod
    def strip_provider_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_secret_action(self):
        if self.api_key is not None and self.clear_api_key:
            raise ValueError("不能同时更新并清除 API Key")
        model_ids = [model.id for model in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("同一提供商不能配置重复模型")
        return self


class LLMSelectionRequest(BaseModel):
    provider_id: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=256)
    reasoning_effort: ReasoningEffort = "auto"


class LLMDefaultsRequest(BaseModel):
    version: int = Field(ge=0)
    agent: LLMSelectionRequest
    reviewer: LLMSelectionRequest


class SessionModelRequest(LLMSelectionRequest):
    version: int = Field(ge=1)


class LLMProviderActionRequest(BaseModel):
    version: int = Field(ge=1)


class PermissionUpdateRequest(BaseModel):
    mode: PermissionMode
    version: int = Field(ge=1)
    trusted_roots: list[str] = Field(default_factory=list, max_length=32)
    ttl_seconds: int | None = Field(default=None, ge=1)


class PermissionResolveRequest(BaseModel):
    decision: PermissionDecision
    context_version: int = Field(ge=1)
    trusted_path: str = ""
    ttl_seconds: int | None = Field(default=None, ge=1)


class PolicyRequest(BaseModel):
    kind: str
    pattern: str
    note: str = ""


class AlertRuleRequest(BaseModel):
    name: str
    metric: str
    operator: str = ">="
    threshold: float = 90.0
    severity: str = "warning"
    silence_minutes: int = 10
    channel_ids: list[int] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def normalize_boolean_metric(self):
        if self.metric == "failed_services":
            self.operator = ">="
            self.threshold = 1.0
        return self


class AlertChannelRequest(BaseModel):
    name: str
    type: str
    config: dict
    enabled: bool = True


def create_app(settings: Settings | None = None,
               *, with_tools: bool = True) -> FastAPI:
    settings = settings or get_settings()
    secure_database_path(settings.db_path)
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("DROP TABLE IF EXISTS users")
    audit = AuditLog(settings.db_path)
    sessions = SessionStore(settings.db_path)
    llm_config = LLMConfigStore(settings.db_path, settings)
    llm_runtime = LLMRuntime(llm_config, settings)
    current_execution_profile = execution_profile_fingerprint(settings)
    # FULL_ACCESS 从不跨后端进程重启继承。sudoers、附加组、capabilities 或
    # 服务沙箱都可能在配置指纹未变化时扩大同一 UID 的权限；重启后主动收回
    # 比尝试穷举所有 OS 授权状态更可靠。kill switch 使用更具体的原因。
    for summary in sessions.list():
        context = sessions.get_permissions(summary["id"])
        if context is None or context.mode != PermissionMode.FULL_ACCESS:
            continue
        revoke_reason = (
            "full_access_disabled"
            if not settings.allow_full_access
            else "service_restarted"
        )
        with audit.serialized():
            with sessions.transaction() as connection:
                fresh = sessions.get_permissions(summary["id"])
                if fresh is None or fresh.mode != PermissionMode.FULL_ACCESS:
                    continue
                changed = sessions.set_permissions(
                    summary["id"],
                    mode=PermissionMode.ASK,
                    trusted_roots=[],
                    expires_at=None,
                    expected_version=fresh.version,
                    updated_by="(server policy)",
                    execution_profile="",
                    commit=False,
                )
                audit.append(
                    summary["id"],
                    "permission_changed",
                    {
                        "operator": "(server policy)",
                        "source": "startup",
                        "from_mode": PermissionMode.FULL_ACCESS.value,
                        "to_mode": changed.mode.value,
                        "reason": revoke_reason,
                    },
                    connection=connection,
                    commit=False,
                    lock_held=True,
                )
    policies = PolicyStore(settings.db_path)
    tools = ToolManager(exec_user=settings.exec_user)
    confirmations = Confirmations()
    permission_requests = PermissionRequests()
    snapshot_cache = SnapshotCache(settings.snapshot_interval)
    alert_rule_store = AlertRuleStore(settings.db_path)
    snapshot_cache.set_rule_store(alert_rule_store)
    planner_llm = llm_runtime.routed_client("agent")
    reviewer_llm = llm_runtime.routed_client("reviewer")
    pipeline = Pipeline(
        settings=settings, audit=audit, tools=tools,
        planner=Planner(planner_llm, settings.max_json_retries),
        reviewer=Reviewer(reviewer_llm, settings.max_json_retries),
        confirmations=confirmations,
        snapshot_fn=snapshot_cache.get,
        policy_store=policies,
        session_store=sessions,
        permission_requests=permission_requests,
        llm_runtime=llm_runtime,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if with_tools:
            await tools.start()
            await snapshot_cache.start()
        yield
        if with_tools:
            await snapshot_cache.stop()
            await tools.stop()
        llm_config.close()
        sessions.close()
        policies.close()
        alert_rule_store.close()
        audit.close()

    app = FastAPI(title="麒盾 KylinGuard", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def safe_validation_error(_request, exc: RequestValidationError):
        """FastAPI 默认 422 会复制原始 input；配置接口可能包含只写 Key。"""
        errors = []
        for item in exc.errors():
            safe = {
                key: value for key, value in item.items()
                if key not in {"input", "ctx"}
            }
            errors.append(safe)
        return JSONResponse(status_code=422, content={"detail": errors})

    app.state.pipeline = pipeline
    app.state.confirmations = confirmations
    app.state.permission_requests = permission_requests
    app.state.audit = audit
    app.state.sessions = sessions
    app.state.snapshot_cache = snapshot_cache
    app.state.alert_rule_store = alert_rule_store
    app.state.policies = policies
    app.state.llm_config = llm_config
    app.state.llm_runtime = llm_runtime

    async def local_operator() -> str:
        """为单用户本机部署提供稳定的审计操作者标识。"""
        return _LOCAL_OPERATOR

    def permission_http_error(
        error: SessionPermissionError, status_code: int = 400
    ) -> HTTPException:
        return HTTPException(
            status_code,
            detail={"code": error.code, "message": error.message},
        )

    def audited_permission_mutation(
        session_id: str,
        event_type: str,
        mutation,
    ):
        """把权限状态变更与哈希链事件提交在同一个 SQLite 事务中。"""
        with app.state.audit.serialized():
            with app.state.sessions.transaction() as connection:
                result, payload = mutation(connection)
                app.state.audit.append(
                    session_id,
                    event_type,
                    payload,
                    connection=connection,
                    commit=False,
                    lock_held=True,
                )
                return result

    def execution_identity() -> tuple[str, str]:
        """返回工具实际使用的 OS 身份及其来源，供权限界面明确展示。"""
        if settings.exec_user:
            return settings.exec_user, "configured_exec_user"
        try:
            username = pwd.getpwuid(os.geteuid()).pw_name
        except (KeyError, OSError):
            username = getpass.getuser()
        return username or "unknown", "backend_process"

    def execution_account_separated() -> bool:
        """只陈述执行账户是否为不同 UID，不把它误称为 ACL 隔离证明。"""
        if not settings.exec_user:
            return False
        try:
            uid = pwd.getpwnam(settings.exec_user).pw_uid
            return uid != os.geteuid()
        except KeyError:
            return False

    def llm_config_payload() -> dict:
        payload = app.state.llm_config.public_config()
        isolated = execution_account_separated()
        payload["security"] = {
            "credentials_isolated": isolated,
            "message": (
                "API Key 由后端保存在受限文件中；Agent 命令使用独立 Linux 账户，"
                "无法以同一账户直接读取这些文件。"
                if isolated else
                "API Key 由后端保存在受限文件中，不会注入工具进程。当前是开发模式："
                "Agent 命令与后端使用同一 Linux 账户；只有开启完全访问时，"
                "才不能保证 Agent 无法读取该账户的其他文件。"
            ),
        }
        return payload

    def execution_grants_root() -> bool:
        if settings.exec_user:
            try:
                return pwd.getpwnam(settings.exec_user).pw_uid == 0
            except KeyError:
                return False
        return os.geteuid() == 0

    def full_access_status() -> tuple[bool, str]:
        if not settings.allow_full_access:
            return False, "服务端已通过 KG_ALLOW_FULL_ACCESS=false 关闭完全访问模式。"
        workspace = Path(settings.workspace_root).expanduser()
        if not workspace.is_absolute() or not workspace.is_dir():
            return False, f"Agent 工作目录不可用：{settings.workspace_root}"
        shell = settings.command_shell
        shell_path = shell if os.path.isabs(shell) else shutil.which(shell)
        if (not shell_path or not Path(shell_path).is_file()
                or not os.access(shell_path, os.X_OK)):
            return False, f"配置的命令 Shell 不可执行：{shell}"
        if settings.exec_user:
            try:
                pwd.getpwnam(settings.exec_user)
            except KeyError:
                return False, f"配置的执行账户不存在：{settings.exec_user}"
            sudo = shutil.which("sudo")
            if sudo is None:
                return False, "配置独立执行账户需要系统安装 sudo。"
            try:
                readiness = subprocess.run(
                    [
                        sudo, "-n", "-H", "-u", settings.exec_user, "--",
                        str(shell_path), "-lc", 'cd -- "$1"',
                        "kylinguard-readiness", str(workspace.resolve()),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=safe_subprocess_env(),
                    timeout=3,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                return False, "无法验证独立执行账户的非交互 sudo 与工作目录访问。"
            if readiness.returncode != 0:
                return False, (
                    "独立执行账户无法通过 sudo -n 启动配置的 Shell，"
                    "或无权进入 Agent 工作目录。"
                )
        return True, ""

    def permission_capabilities_payload() -> dict:
        """返回与具体会话无关的权限能力事实，供新任务创建前展示。"""
        enabled, reason = full_access_status()
        identity, identity_source = execution_identity()
        return {
            "full_access_available": enabled,
            "full_access_unavailable_reason": reason,
            "full_access_max_ttl": settings.full_access_max_ttl,
            "permission_default_ttl": settings.permission_default_ttl,
            "permission_max_ttl": settings.permission_max_ttl,
            "execution_identity": identity,
            "execution_identity_source": identity_source,
            "workspace_root": settings.workspace_root,
            "command_shell": settings.command_shell,
            "command_max_timeout": settings.command_max_timeout,
            "full_access_capabilities": [
                "shell", "files", "network", "processes",
            ],
            "execution_account_separated": execution_account_separated(),
            # 兼容旧前端；不同 UID 并不能证明 DrvFS/组权限/ACL 已隔离控制面。
            "control_plane_isolated": False,
            "grants_root": execution_grants_root(),
        }

    def resolve_session_workspace(value: str = "") -> str:
        """校验后端/WSL 可见的会话工作目录；它是上下文而非沙箱边界。"""
        raw = (value or settings.workspace_root).strip()
        if not raw or "\x00" in raw:
            raise SessionPermissionError(
                "invalid_workspace_root", "工作目录必须是非空绝对路径。"
            )
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            raise SessionPermissionError(
                "invalid_workspace_root", "工作目录必须是服务器绝对路径。"
            )
        try:
            resolved = candidate.resolve(strict=True)
        except (FileNotFoundError, OSError, RuntimeError) as exc:
            raise SessionPermissionError(
                "workspace_root_unavailable", f"工作目录不存在或无法访问：{raw}"
            ) from exc
        if not resolved.is_dir():
            raise SessionPermissionError(
                "workspace_root_unavailable", f"工作目录不是目录：{raw}"
            )
        return str(resolved)

    def permission_payload(context) -> dict:
        payload = {
            **context.model_dump(mode="json"),
            **permission_capabilities_payload(),
        }
        payload["workspace_root"] = (
            app.state.sessions.get_workspace_root(context.session_id)
            or settings.workspace_root
        )
        return payload

    def llm_http_error(error: LLMConfigError) -> HTTPException:
        return HTTPException(
            error.status_code,
            detail={"code": error.code, "message": error.message},
        )

    def model_selection(provider_id: str, model_id: str,
                        reasoning_effort: str) -> ModelSelection | None:
        if not provider_id and not model_id:
            return None
        return ModelSelection(provider_id, model_id, reasoning_effort)

    def default_agent_selection() -> ModelSelection:
        raw = app.state.llm_config.get_defaults()["agent"]
        if not raw["provider_id"] or not raw["model_id"]:
            raise llm_http_error(LLMConfigError(
                "model_configuration_required",
                "尚未配置默认模型，请先在“模型服务”中添加提供商和模型。",
                status_code=409,
            ))
        return ModelSelection(
            raw["provider_id"], raw["model_id"], raw["reasoning_effort"])

    def audit_llm_config(event_type: str, user: str, **payload) -> None:
        # 白名单调用点只传元数据；禁止把 Pydantic 请求整体写入审计链。
        app.state.audit.append("__llm_config__", event_type, {
            "operator": user,
            **payload,
        })

    def transactional_llm_audit(
        session_id: str, event_type: str, user: str, payload_builder,
    ):
        """生成在 LLM 配置事务内写入同库审计链的回调。"""
        def append(value, connection):
            app.state.audit.append(
                session_id,
                event_type,
                {"operator": user, **payload_builder(value)},
                connection=connection,
                commit=False,
                lock_held=True,
            )
        return append

    def permission_expiry(mode: PermissionMode, ttl_seconds: int | None) -> float | None:
        if mode not in {
            PermissionMode.TRUSTED_WORKSPACE,
            PermissionMode.FULL_ACCESS,
        }:
            if ttl_seconds is not None:
                raise SessionPermissionError(
                    "permission_ttl_not_applicable", "当前权限模式不需要有效期。"
                )
            return None
        ttl = ttl_seconds or settings.permission_default_ttl
        maximum = (settings.full_access_max_ttl
                   if mode == PermissionMode.FULL_ACCESS
                   else settings.permission_max_ttl)
        if ttl > maximum:
            raise SessionPermissionError(
                "permission_ttl_too_long",
                f"该权限模式的有效期不能超过 {maximum} 秒。",
            )
        return expires_after(ttl)

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    # ---- 模型服务与运行时配置 ----

    @app.get("/api/llm/config")
    async def get_llm_config(_user: str = Depends(local_operator)):
        return llm_config_payload()

    @app.post("/api/llm/providers", status_code=201)
    async def create_llm_provider(
        req: LLMProviderRequest, user: str = Depends(local_operator),
    ):
        api_key = (req.api_key.get_secret_value()
                   if req.api_key is not None else "")
        try:
            with app.state.audit.serialized():
                provider = app.state.llm_config.create_provider(
                    name=req.name,
                    adapter=req.adapter,
                    base_url=req.base_url,
                    api_key=api_key,
                    models=[model.model_dump() for model in req.models],
                    enabled=req.enabled,
                    allow_insecure_http=req.allow_insecure_http,
                    updated_by=user,
                    audit=transactional_llm_audit(
                        "__llm_config__", "llm_provider_created", user,
                        lambda item: {
                            "provider_id": item["id"], "name": item["name"],
                            "adapter": item["adapter"],
                            "models": [model["id"] for model in item["models"]],
                            "api_key_changed": bool(api_key),
                        },
                    ),
                )
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc
        return {"provider": provider}

    @app.put("/api/llm/providers/{provider_id}")
    async def update_llm_provider(
        provider_id: str, req: LLMProviderRequest,
        user: str = Depends(local_operator),
    ):
        if req.version is None:
            raise HTTPException(422, detail={
                "code": "provider_version_required",
                "message": "编辑提供商必须携带当前版本。",
            })
        api_key = (req.api_key.get_secret_value()
                   if req.api_key is not None else None)
        try:
            with app.state.audit.serialized():
                provider = app.state.llm_config.update_provider(
                    provider_id,
                    expected_version=req.version,
                    name=req.name,
                    adapter=req.adapter,
                    base_url=req.base_url,
                    models=[model.model_dump() for model in req.models],
                    enabled=req.enabled,
                    allow_insecure_http=req.allow_insecure_http,
                    api_key=api_key,
                    clear_api_key=req.clear_api_key,
                    updated_by=user,
                    audit=transactional_llm_audit(
                        "__llm_config__", "llm_provider_updated", user,
                        lambda item: {
                            "provider_id": provider_id,
                            "name": item["name"], "adapter": item["adapter"],
                            "version": item["version"],
                            "models": [model["id"] for model in item["models"]],
                            "api_key_changed": bool(api_key) or req.clear_api_key,
                        },
                    ),
                )
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc
        return {"provider": provider}

    @app.delete("/api/llm/providers/{provider_id}")
    async def delete_llm_provider(
        provider_id: str, req: LLMProviderActionRequest,
        user: str = Depends(local_operator),
    ):
        try:
            with app.state.audit.serialized():
                app.state.llm_config.delete_provider(
                    provider_id,
                    expected_version=req.version,
                    audit=transactional_llm_audit(
                        "__llm_config__", "llm_provider_deleted", user,
                        lambda item: {
                            "provider_id": provider_id,
                            "name": item["name"], "adapter": item["adapter"],
                        },
                    ),
                )
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc
        return {"ok": True}

    def provider_action_error(exc: Exception) -> HTTPException:
        if isinstance(exc, LLMConfigError):
            return llm_http_error(exc)
        error = public_error_from_exception(exc)
        status = 503 if error.retryable else 400
        return HTTPException(status, detail=error.to_dict())

    @app.post("/api/llm/providers/{provider_id}/test")
    async def test_llm_provider(
        provider_id: str, req: LLMProviderActionRequest,
        user: str = Depends(local_operator),
    ):
        try:
            provider = app.state.llm_config.get_provider(provider_id)
            if provider["version"] != req.version:
                raise LLMConfigVersionConflict()
            result = await app.state.llm_runtime.test_provider(provider_id)
            if app.state.llm_config.get_provider(provider_id)["version"] != req.version:
                raise LLMConfigVersionConflict()
        except Exception as exc:
            raise provider_action_error(exc) from exc
        audit_llm_config(
            "llm_provider_tested", user,
            provider_id=provider_id, ok=True,
            latency_ms=result["latency_ms"],
            model_count=result["model_count"],
        )
        return result

    @app.post("/api/llm/providers/{provider_id}/discover-models")
    async def discover_llm_models(
        provider_id: str, req: LLMProviderActionRequest,
        user: str = Depends(local_operator),
    ):
        try:
            provider = app.state.llm_config.get_provider(provider_id)
            if provider["version"] != req.version:
                raise LLMConfigVersionConflict()
            ids = await app.state.llm_runtime.fetch_model_ids(provider_id)
            with app.state.audit.serialized():
                updated_provider = app.state.llm_config.add_discovered_models(
                    provider_id,
                    ids,
                    expected_version=req.version,
                    updated_by=user,
                    audit=transactional_llm_audit(
                        "__llm_config__", "llm_models_discovered", user,
                        lambda item: {
                            "provider_id": provider_id,
                            "models": [model["id"] for model in item["models"]],
                        },
                    ),
                )
            result = {"provider": updated_provider, "discovered": len(ids)}
        except Exception as exc:
            raise provider_action_error(exc) from exc
        return result

    @app.put("/api/llm/defaults")
    async def update_llm_defaults(
        req: LLMDefaultsRequest, user: str = Depends(local_operator),
    ):
        try:
            with app.state.audit.serialized():
                defaults = app.state.llm_config.update_defaults(
                    agent=ModelSelection(
                        req.agent.provider_id, req.agent.model_id,
                        req.agent.reasoning_effort),
                    reviewer=ModelSelection(
                        req.reviewer.provider_id, req.reviewer.model_id,
                        req.reviewer.reasoning_effort),
                    expected_version=req.version,
                    updated_by=user,
                    audit=transactional_llm_audit(
                        "__llm_config__", "llm_defaults_updated", user,
                        lambda item: {
                            "version": item["version"],
                            "agent": item["agent"],
                            "reviewer": item["reviewer"],
                        },
                    ),
                )
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc
        return llm_config_payload()

    @app.post("/api/chat")
    async def chat(req: ChatRequest, user: str = Depends(local_operator)):
        created = not req.session_id
        requested_model = model_selection(
            req.provider_id, req.model_id, req.reasoning_effort)
        if created and requested_model is None:
            requested_model = default_agent_selection()
        if requested_model is not None:
            try:
                app.state.llm_config.validate_selection(requested_model)
            except LLMConfigError as exc:
                raise llm_http_error(exc) from exc
        if created:
            session_id = uuid.uuid4().hex
            initial_mode = req.permission_mode or PermissionMode.ASK
            try:
                workspace_root = resolve_session_workspace(req.workspace_root)
                initial_expiry = permission_expiry(
                    initial_mode, req.permission_ttl_seconds)
                if req.permission_mode is None:
                    # 与 sessions 行共用一个 BEGIN IMMEDIATE 事务，避免模型
                    # 绑定失败后留下前端不知道的幽灵会话。
                    with app.state.sessions.transaction() as connection:
                        app.state.sessions.create(
                            session_id, req.message, workspace_root=workspace_root,
                            commit=False,
                        )
                        session_model = (
                            app.state.llm_config.create_session_with_connection(
                                connection, session_id, requested_model,
                                updated_by=user,
                            )
                        )
                else:
                    def create_session_with_permissions(connection):
                        app.state.sessions.create(
                            session_id,
                            req.message,
                            permission_mode=initial_mode,
                            trusted_roots=req.trusted_roots,
                            permission_expires_at=initial_expiry,
                            updated_by=user,
                            workspace_root=workspace_root,
                            commit=False,
                        )
                        context = app.state.sessions.get_permissions(session_id)
                        session_model = app.state.llm_config.create_session_with_connection(
                            connection, session_id, requested_model,
                            updated_by=user,
                        )
                        return (context, session_model), {
                            "operator": user,
                            "source": "session_created",
                            "from_mode": None,
                            "to_mode": initial_mode.value,
                            "trusted_roots": context.trusted_roots,
                            "expires_at": context.expires_at,
                            "version": context.version,
                        }

                    context, session_model = audited_permission_mutation(
                        session_id,
                        "permission_changed",
                        create_session_with_permissions,
                    )
            except SessionPermissionError as exc:
                raise permission_http_error(exc) from exc
            except LLMConfigError as exc:
                raise llm_http_error(exc) from exc
        else:
            session_id = req.session_id
            if not app.state.sessions.exists(session_id):
                raise HTTPException(404, "会话不存在")
            if (req.permission_mode is not None or req.trusted_roots
                    or req.permission_ttl_seconds is not None
                    or req.workspace_root or req.provider_id or req.model_id
                    or req.reasoning_effort != "auto"):
                raise HTTPException(
                    400,
                    detail={
                        "code": "permission_update_requires_endpoint",
                        "message": "已有会话请通过专用接口更新权限或模型。",
                    },
                )
            try:
                session_model = app.state.llm_config.ensure_session(
                    session_id, updated_by=user)
            except LLMConfigError as exc:
                raise llm_http_error(exc) from exc
            app.state.sessions.touch(session_id, first_message=req.message)
        queue: asyncio.Queue = asyncio.Queue()
        last_progress: dict = {}
        pending_confirm: dict | None = None

        async def emit(event: dict):
            nonlocal last_progress, pending_confirm
            if event.get("type") == "progress":
                last_progress = {
                    key: event[key]
                    for key in ("stage", "operation_id", "step_id", "tool")
                    if event.get(key) is not None
                }
            elif event.get("type") == "confirm_request":
                pending_confirm = {
                    "confirm_id": event.get("confirm_id"),
                    "step_id": event.get("step_id"),
                }
            elif (event.get("type") == "confirm_result"
                  and pending_confirm
                  and event.get("confirm_id") == pending_confirm["confirm_id"]):
                pending_confirm = None
            await queue.put(event)

        async def run():
            started = time.monotonic()
            try:
                await app.state.pipeline.handle(session_id, req.message, emit)
            except asyncio.CancelledError:
                # SSE 断开会停止本轮流水线；即使客户端已收不到事件，
                # 也必须在审计中留下明确终态，避免历史会话看似悬空。
                elapsed_ms = int((time.monotonic() - started) * 1000)
                try:
                    if pending_confirm:
                        app.state.audit.append(session_id, "confirm_result", {
                            **pending_confirm,
                            "approved": False,
                            "operator": "(任务取消)",
                            "cancelled": True,
                            "timed_out": False,
                        })
                    cancelled_payload = {
                        "reason": "client_disconnected",
                        "elapsed_ms": elapsed_ms,
                        **last_progress,
                    }
                    if req.request_id:
                        cancelled_payload["request_id"] = req.request_id
                    app.state.audit.append(
                        session_id, "task_cancelled", cancelled_payload)
                    app.state.audit.append(session_id, "final_answer", {
                        "answer": ("客户端连接已中断，本轮任务已停止。"
                                   "已经开始的系统操作不会自动回滚。"),
                        "aborted": True,
                        "outcome": "cancelled",
                        "elapsed_ms": elapsed_ms,
                    })
                except AuditError:
                    pass
                raise
            except AuditError:
                await queue.put({"type": "fatal",
                                 "error": "审计写入失败，任务已中止。",
                                 "request_id": req.request_id})
            except Exception as exc:  # 未知错误也必须形成可回放的安全终态
                if isinstance(exc, LLMError):
                    error = exc.error
                elif isinstance(exc, LLMConfigError):
                    error = public_error(
                        "llm_config_invalid", exc.message, retryable=False)
                else:
                    error = internal_error()
                elapsed_ms = int((time.monotonic() - started) * 1000)
                logger.error(
                    "chat task failed incident_id=%s exception_type=%s",
                    error.incident_id, type(exc).__name__,
                )
                try:
                    task_payload = {
                        "stage": "internal",
                        "operation_id": "task",
                        "elapsed_ms": elapsed_ms,
                        "error": error.to_dict(),
                    }
                    if req.request_id:
                        task_payload["request_id"] = req.request_id
                    h = app.state.audit.append(
                        session_id, "task_error", task_payload)
                    await queue.put({
                        "type": "task_error",
                        "session_id": session_id,
                        "hash": h,
                        **task_payload,
                    })
                    final_payload = {
                        "answer": (f"{error.message} 错误编号："
                                   f"{error.incident_id}"),
                        "aborted": True,
                        "outcome": "failed",
                        "elapsed_ms": elapsed_ms,
                    }
                    h = app.state.audit.append(
                        session_id, "final_answer", final_payload)
                    await queue.put({
                        "type": "final_answer",
                        "session_id": session_id,
                        "hash": h,
                        **final_payload,
                    })
                except AuditError:
                    await queue.put({
                        "type": "fatal",
                        "error": "审计写入失败，任务已中止。",
                        "request_id": req.request_id,
                    })
            finally:
                await queue.put(None)

        async def stream():
            if created:
                yield ("data: " + json.dumps(
                    {"type": "session_created", "session_id": session_id,
                     "request_id": req.request_id,
                     "model_context": session_model},
                    ensure_ascii=False) + "\n\n")
            task = asyncio.create_task(run())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    if (req.request_id
                            and event.get("type") in {"task_error", "fatal"}):
                        event = {**event, "request_id": req.request_id}
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield 'data: {"type": "done"}\n\n'
            finally:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/confirm")
    async def confirm(req: ConfirmRequest,
                      user: str = Depends(local_operator)):
        # operator 随 confirm_result 写入审计链：确认决断归因到管理员账号
        return {"ok": app.state.confirmations.resolve(
            req.confirm_id, req.approved, operator=user)}

    @app.post("/api/sessions", status_code=201)
    async def create_draft_session(
        req: SessionCreateRequest,
        user: str = Depends(local_operator),
    ):
        """原子创建首条消息前的完全访问会话。"""
        available, reason = full_access_status()
        if not available:
            raise HTTPException(403, detail={
                "code": "full_access_disabled", "message": reason,
            })
        requested_model = model_selection(
            req.provider_id, req.model_id, req.reasoning_effort)
        if requested_model is None:
            requested_model = default_agent_selection()
        if requested_model is not None:
            try:
                app.state.llm_config.validate_selection(requested_model)
            except LLMConfigError as exc:
                raise llm_http_error(exc) from exc
        try:
            workspace_root = resolve_session_workspace(req.workspace_root)
            expiry = permission_expiry(req.mode, req.ttl_seconds)

            def create_with_full_access(connection):
                app.state.sessions.create(
                    req.session_id,
                    "新任务",
                    permission_mode=PermissionMode.FULL_ACCESS,
                    permission_expires_at=expiry,
                    permission_execution_profile=current_execution_profile,
                    updated_by=user,
                    draft=True,
                    workspace_root=workspace_root,
                    strict=True,
                    commit=False,
                )
                context = app.state.sessions.get_permissions(req.session_id)
                assert context is not None
                session_model = app.state.llm_config.create_session_with_connection(
                    connection, req.session_id, requested_model,
                    updated_by=user,
                )
                return (context, session_model), {
                    "operator": user,
                    "source": "pre_message",
                    "from_mode": None,
                    "to_mode": PermissionMode.FULL_ACCESS.value,
                    "trusted_roots": [],
                    "expires_at": context.expires_at,
                    "version": context.version,
                    "draft": True,
                }

            context, session_model = audited_permission_mutation(
                req.session_id,
                "permission_changed",
                create_with_full_access,
            )
        except SessionPermissionError as exc:
            status = 409 if exc.code == "session_already_exists" else 400
            raise permission_http_error(exc, status) from exc
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc
        return {
            "session_id": req.session_id,
            "draft": True,
            "permission": permission_payload(context),
            "model_context": session_model,
        }

    @app.get("/api/sessions")
    async def list_sessions(_user: str = Depends(local_operator)):
        summaries = app.state.sessions.list()
        for summary in summaries:
            try:
                summary["model"] = app.state.llm_config.get_session(
                    summary["id"], ensure=True)
            except LLMConfigError:
                # 配置被外部破坏时任务列表仍可用；进入会话后模型接口会返回
                # 明确诊断，绝不静默换到另一个提供商。
                summary["model"] = None
        return {
            "sessions": summaries,
            "permission_capabilities": permission_capabilities_payload(),
        }

    @app.get("/api/sessions/{session_id}/model")
    async def get_session_model(
        session_id: str, _user: str = Depends(local_operator),
    ):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        try:
            return app.state.llm_config.get_session(session_id, ensure=True)
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc

    @app.put("/api/sessions/{session_id}/model")
    async def update_session_model(
        session_id: str, req: SessionModelRequest,
        user: str = Depends(local_operator),
    ):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        is_busy = getattr(app.state.pipeline, "session_busy", lambda _id: False)
        if is_busy(session_id):
            raise HTTPException(409, detail={
                "code": "session_busy",
                "message": "当前回合仍在运行，模型切换将在任务结束后开放。",
            })
        try:
            with app.state.audit.serialized():
                context = app.state.llm_config.update_session(
                    session_id,
                    selection=ModelSelection(
                        req.provider_id, req.model_id, req.reasoning_effort),
                    expected_version=req.version,
                    updated_by=user,
                    audit=transactional_llm_audit(
                        session_id, "session_model_changed", user,
                        lambda item: {
                            "provider_id": item["provider_id"],
                            "model_id": item["model_id"],
                            "reasoning_effort": item["reasoning_effort"],
                            "version": item["version"],
                        },
                    ),
                )
        except LLMConfigError as exc:
            raise llm_http_error(exc) from exc
        return context

    @app.get("/api/sessions/{session_id}/permissions")
    async def get_session_permissions(
        session_id: str, _user: str = Depends(local_operator)
    ):
        context = app.state.sessions.get_permissions(session_id)
        if context is None:
            raise HTTPException(404, "会话不存在")
        return permission_payload(context)

    @app.put("/api/sessions/{session_id}/permissions")
    async def update_session_permissions(
        session_id: str,
        req: PermissionUpdateRequest,
        user: str = Depends(local_operator),
    ):
        previous = app.state.sessions.get_permissions(session_id)
        if previous is None:
            raise HTTPException(404, "会话不存在")
        if req.mode == PermissionMode.FULL_ACCESS:
            available, reason = full_access_status()
            if not available:
                raise HTTPException(
                    403, detail={
                        "code": "full_access_disabled", "message": reason,
                    })
        try:
            expiry = permission_expiry(req.mode, req.ttl_seconds)
            def mutate_permissions(_connection):
                context = app.state.sessions.set_permissions(
                    session_id,
                    mode=req.mode,
                    trusted_roots=req.trusted_roots,
                    expires_at=expiry,
                    expected_version=req.version,
                    updated_by=user,
                    execution_profile=(
                        current_execution_profile
                        if req.mode == PermissionMode.FULL_ACCESS else ""
                    ),
                    commit=False,
                )
                return context, {
                    "operator": user,
                    "source": "settings",
                    "from_mode": previous.mode.value,
                    "to_mode": context.mode.value,
                    "trusted_roots": context.trusted_roots,
                    "expires_at": context.expires_at,
                    "previous_version": previous.version,
                    "version": context.version,
                }

            context = audited_permission_mutation(
                session_id, "permission_changed", mutate_permissions)
        except PermissionVersionConflict as exc:
            raise permission_http_error(exc, 409) from exc
        except SessionPermissionError as exc:
            status = 404 if exc.code == "session_not_found" else 400
            raise permission_http_error(exc, status) from exc
        cancelled_requests = app.state.permission_requests.revoke_session(
            session_id, operator=user)
        return permission_payload(context)

    @app.get("/api/sessions/{session_id}/grants")
    async def list_session_grants(
        session_id: str,
        include_inactive: bool = False,
        _user: str = Depends(local_operator),
    ):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        grants = app.state.sessions.list_grants(
            session_id, active_only=not include_inactive)
        return {"grants": [grant.model_dump(mode="json") for grant in grants]}

    @app.delete("/api/sessions/{session_id}/grants")
    async def revoke_session_grants(
        session_id: str, user: str = Depends(local_operator)
    ):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        def mutate_revoke_all(_connection):
            count = app.state.sessions.revoke_grants(
                session_id, commit=False)
            return count, {
                "operator": user,
                "scope": "all",
                "revoked_grants": count,
            }

        count = audited_permission_mutation(
            session_id, "permission_grants_revoked", mutate_revoke_all)
        cancelled_requests = app.state.permission_requests.revoke_session(
            session_id, operator=user)
        return {"ok": True, "revoked": count,
                "cancelled_requests": cancelled_requests}

    @app.delete("/api/sessions/{session_id}/grants/{grant_id}")
    async def revoke_session_grant(
        session_id: str, grant_id: str,
        user: str = Depends(local_operator),
    ):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        def mutate_revoke_one(_connection):
            count = app.state.sessions.revoke_grants(
                session_id, grant_id, commit=False)
            if not count:
                raise HTTPException(404, detail={
                    "code": "permission_grant_not_found",
                    "message": "授权不存在、已使用或已撤销。",
                })
            return count, {
                "operator": user,
                "scope": "single",
                "grant_id": grant_id,
                "revoked_grants": count,
            }

        count = audited_permission_mutation(
            session_id, "permission_grants_revoked", mutate_revoke_one)
        return {"ok": True, "revoked": count}

    @app.post("/api/permission-requests/{request_id}/resolve")
    async def resolve_permission_request(
        request_id: str,
        req: PermissionResolveRequest,
        user: str = Depends(local_operator),
    ):
        pending = app.state.permission_requests.get(request_id)
        if pending is None:
            raise HTTPException(404, detail={
                "code": "permission_request_not_found",
                "message": "权限请求不存在或已经处理。",
            })
        context = app.state.sessions.get_permissions(pending.session_id)
        if context is None:
            app.state.permission_requests.cancel(request_id, operator=user)
            raise HTTPException(404, "会话不存在")
        if (req.context_version != pending.context_version
                or context.version != pending.context_version):
            app.state.permission_requests.cancel(request_id, operator=user)
            app.state.audit.append(pending.session_id,
                                   "permission_request_stale", {
                "operator": user,
                "request_id": request_id,
                "request_version": pending.context_version,
                "current_version": context.version,
            })
            raise permission_http_error(PermissionVersionConflict(), 409)

        if (pending.single_action_only
                and req.decision != PermissionDecision.DENY):
            if req.decision != PermissionDecision.ALLOW_ONCE:
                raise HTTPException(400, detail={
                    "code": "high_risk_scope_not_allowed",
                    "message": "高风险操作只能按当前动作单次授权。",
                })

        grant = None
        trusted_path = None
        changed_context = None
        try:
            with app.state.audit.serialized():
                with app.state.sessions.transaction() as connection:
                    context = app.state.sessions.get_permissions(
                        pending.session_id)
                    if (context is None
                            or context.version != pending.context_version):
                        raise PermissionVersionConflict()
                    if req.decision == PermissionDecision.DENY:
                        if req.trusted_path or req.ttl_seconds is not None:
                            raise SessionPermissionError(
                                "unexpected_permission_options",
                                "拒绝操作时不能附带可信目录或有效期。",
                            )
                    elif req.decision in {
                        PermissionDecision.ALLOW_ONCE,
                        PermissionDecision.ALLOW_SESSION,
                    }:
                        if req.trusted_path or req.ttl_seconds is not None:
                            raise SessionPermissionError(
                                "unexpected_permission_options",
                                "单次或会话授权不能附带可信目录或自定义有效期。",
                            )
                        grant_expiry = expires_after(
                            settings.permission_default_ttl)
                        if (not context.expired
                                and context.expires_at is not None):
                            grant_expiry = min(
                                grant_expiry, context.expires_at)
                        grant = app.state.sessions.add_grant(
                            pending.session_id,
                            scope=(PermissionGrantScope.ONCE
                                   if req.decision == PermissionDecision.ALLOW_ONCE
                                   else PermissionGrantScope.SESSION),
                            action_fingerprint=pending.action_fingerprint,
                            capability=pending.capability,
                            resource=pending.resource,
                            context_version=pending.context_version,
                            granted_by=user,
                            expires_at=grant_expiry,
                            commit=False,
                        )
                    elif req.decision == PermissionDecision.TRUST_PATH:
                        trusted_path = normalize_trusted_root(
                            req.trusted_path or pending.suggested_path)
                        roots = (list(context.trusted_roots)
                                 if context.mode == PermissionMode.TRUSTED_WORKSPACE
                                 and not context.expired else [])
                        roots.append(trusted_path)
                        if (req.ttl_seconds is None
                                and context.mode == PermissionMode.TRUSTED_WORKSPACE
                                and not context.expired):
                            expiry = context.expires_at
                        else:
                            expiry = permission_expiry(
                                PermissionMode.TRUSTED_WORKSPACE,
                                req.ttl_seconds,
                            )
                        changed_context = app.state.sessions.set_permissions(
                            pending.session_id,
                            mode=PermissionMode.TRUSTED_WORKSPACE,
                            trusted_roots=roots,
                            expires_at=expiry,
                            expected_version=pending.context_version,
                            updated_by=user,
                            commit=False,
                        )

                    resolution = PermissionResolution(
                        request_id=request_id,
                        decision=req.decision,
                        operator=user,
                        context_version=pending.context_version,
                        grant_id=grant.id if grant else None,
                        trusted_path=trusted_path,
                    )
                    app.state.audit.append(
                        pending.session_id,
                        "permission_resolved",
                        {
                            "operator": user,
                            "request_id": request_id,
                            "decision": req.decision.value,
                            "action_fingerprint": pending.action_fingerprint,
                            "capability": pending.capability,
                            "resource": pending.resource,
                            "context_version": pending.context_version,
                            "grant_id": grant.id if grant else None,
                            "trusted_path": trusted_path,
                            "new_context_version": (
                                changed_context.version
                                if changed_context else None),
                        },
                        connection=connection,
                        commit=False,
                        lock_held=True,
                    )
        except PermissionVersionConflict as exc:
            app.state.permission_requests.cancel(request_id, operator=user)
            raise permission_http_error(exc, 409) from exc
        except SessionPermissionError as exc:
            raise permission_http_error(exc) from exc
        # Future 必须在 SQLite 状态与审计事件均提交成功后才能唤醒流水线。
        # 此处到 resolve 之间没有 await，同一事件循环上的并发请求不能插入。
        if not app.state.permission_requests.resolve(resolution):
            if grant is not None:
                def revoke_unclaimed_grant(_connection):
                    count = app.state.sessions.revoke_grants(
                        pending.session_id, grant.id, commit=False)
                    return count, {
                        "operator": "system",
                        "scope": "unclaimed_resolution",
                        "grant_id": grant.id,
                        "revoked_grants": count,
                    }
                audited_permission_mutation(
                    pending.session_id,
                    "permission_grants_revoked",
                    revoke_unclaimed_grant,
                )
            raise HTTPException(409, detail={
                "code": "permission_request_already_resolved",
                "message": "权限请求已被其他操作处理。",
            })
        return {
            "ok": True,
            "resolution": resolution.model_dump(mode="json"),
            "permission": (permission_payload(changed_context)
                           if changed_context else None),
        }

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str,
                             _user: str = Depends(local_operator)):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        return {"events": app.state.audit.events(session_id)}

    @app.get("/api/sessions/{session_id}/verify")
    async def session_verify(session_id: str,
                             _user: str = Depends(local_operator)):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        return {"ok": app.state.audit.verify_chain(session_id)}

    @app.get("/api/stats")
    async def stats(_user: str = Depends(local_operator)):
        return {"sessions": len(app.state.sessions.list()),
                **app.state.audit.stats()}

    @app.get("/api/status")
    async def status(_user: str = Depends(local_operator)):
        import json as _j
        snapshot, age = await app.state.snapshot_cache.get()
        body = _j.dumps({"snapshot": snapshot,
                          "collected_ago_seconds": round(age, 1)},
                        ensure_ascii=False)
        from fastapi.responses import Response as _R
        return _R(content=body.encode("utf-8"), media_type="application/json")

    @app.get("/api/alerts")
    async def list_alerts(_user: str = Depends(local_operator)):
        return {"alerts": app.state.snapshot_cache.alert_store.active()}

    @app.post("/api/alerts/{alert_id}/ack")
    async def ack_alert(alert_id: str, _user: str = Depends(local_operator)):
        if not app.state.snapshot_cache.alert_store.ack(alert_id):
            raise HTTPException(404, "告警不存在")
        return {"ok": True}

    # ---- 告警规则 ----

    @app.get("/api/alert-rules")
    async def list_alert_rules(_user: str = Depends(local_operator)):
        rules = app.state.alert_rule_store.list_rules()
        return {"rules": [vars(r) for r in rules]}

    @app.post("/api/alert-rules")
    async def create_alert_rule(req: AlertRuleRequest,
                                _user: str = Depends(local_operator)):
        rid = app.state.alert_rule_store.add_rule(
            req.name, req.metric, req.operator, req.threshold,
            req.severity, req.silence_minutes, req.channel_ids, req.enabled)
        return {"id": rid}

    @app.put("/api/alert-rules/{rule_id}")
    async def update_alert_rule(rule_id: int, req: AlertRuleRequest,
                                _user: str = Depends(local_operator)):
        ok = app.state.alert_rule_store.update_rule(
            rule_id, name=req.name, metric=req.metric, operator=req.operator,
            threshold=req.threshold, severity=req.severity,
            silence_minutes=req.silence_minutes,
            channel_ids=req.channel_ids, enabled=req.enabled)
        if not ok:
            raise HTTPException(404, "规则不存在")
        return {"ok": True}

    @app.delete("/api/alert-rules/{rule_id}")
    async def delete_alert_rule(rule_id: int, _user: str = Depends(local_operator)):
        if not app.state.alert_rule_store.delete_rule(rule_id):
            raise HTTPException(404, "规则不存在")
        return {"ok": True}

    # ---- 推送渠道 ----

    @app.get("/api/alert-channels")
    async def list_alert_channels(_user: str = Depends(local_operator)):
        channels = app.state.alert_rule_store.list_channels()
        return {"channels": [vars(c) for c in channels]}

    @app.post("/api/alert-channels")
    async def create_alert_channel(req: AlertChannelRequest,
                                   _user: str = Depends(local_operator)):
        cid = app.state.alert_rule_store.add_channel(
            req.name, req.type, req.config, req.enabled)
        return {"id": cid}

    @app.put("/api/alert-channels/{ch_id}")
    async def update_alert_channel(ch_id: int, req: AlertChannelRequest,
                                   _user: str = Depends(local_operator)):
        ok = app.state.alert_rule_store.update_channel(
            ch_id, name=req.name, type=req.type,
            config=req.config, enabled=req.enabled)
        if not ok:
            raise HTTPException(404, "渠道不存在")
        return {"ok": True}

    @app.delete("/api/alert-channels/{ch_id}")
    async def delete_alert_channel(ch_id: int,
                                   _user: str = Depends(local_operator)):
        if not app.state.alert_rule_store.delete_channel(ch_id):
            raise HTTPException(404, "渠道不存在")
        return {"ok": True}

    @app.post("/api/alert-channels/{ch_id}/test")
    async def test_alert_channel(ch_id: int, _user: str = Depends(local_operator)):
        ch = app.state.alert_rule_store.get_channel(ch_id)
        if not ch:
            raise HTTPException(404, "渠道不存在")
        payload = {
            "rule_name": "测试推送",
            "metric": "test", "metric_value": "—",
            "severity": "warning", "title": "KylinGuard 测试告警",
            "message": "这是一条来自 KylinGuard 的测试推送，渠道配置正确。",
        }
        ok, msg = await push_channel(ch, payload)
        return {"ok": ok, "message": msg}

    # ---- 告警历史 ----

    @app.get("/api/alert-history")
    async def list_alert_history(_user: str = Depends(local_operator)):
        entries = app.state.alert_rule_store.list_history()
        return {"history": [vars(e) for e in entries]}

    @app.delete("/api/alert-history")
    async def clear_alert_history(_user: str = Depends(local_operator)):
        app.state.alert_rule_store.clear_history()
        return {"ok": True}

    @app.get("/api/policies")
    async def list_policies(_user: str = Depends(local_operator)):
        return {"custom": app.state.policies.list(),
                "builtin": builtin_rules(), "kinds": list(KINDS)}

    @app.post("/api/policies")
    async def add_policy(req: PolicyRequest,
                         user: str = Depends(local_operator)):
        try:
            with app.state.audit.serialized():
                with app.state.policies.transaction() as connection:
                    pid = app.state.policies.add(
                        req.kind, req.pattern, req.note, commit=False,
                    )
                    app.state.audit.append(
                        "__policies__",
                        "policy_added",
                        {
                            "operator": user,
                            "policy_id": pid,
                            "kind": req.kind,
                            "pattern": req.pattern,
                            "note": req.note,
                        },
                        connection=connection,
                        commit=False,
                        lock_held=True,
                    )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": pid}

    @app.delete("/api/policies/{policy_id}")
    async def delete_policy(policy_id: int,
                            user: str = Depends(local_operator)):
        with app.state.audit.serialized():
            with app.state.policies.transaction() as connection:
                policy = app.state.policies.get(policy_id)
                if policy is None:
                    raise HTTPException(404, "策略不存在")
                app.state.policies.remove(policy_id, commit=False)
                app.state.audit.append(
                    "__policies__",
                    "policy_removed",
                    {
                        "operator": user,
                        "policy_id": policy_id,
                        "kind": policy["kind"],
                        "pattern": policy["pattern"],
                        "note": policy["note"],
                    },
                    connection=connection,
                    commit=False,
                    lock_held=True,
                )
        return {"ok": True}

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True),
                  name="frontend")

    return app
