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
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from kylinguard.audit import AuditError, AuditLog
from kylinguard.authorization import execution_profile_fingerprint
from kylinguard.config import Settings, get_settings
from kylinguard.context_files import (
    MAX_CONTEXT_FILES,
    MAX_CONTEXT_MENTIONS,
    ContextFileError,
    ContextMentionError,
    normalize_context_mentions,
    search_context_files,
    validate_context_files,
)
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
from kylinguard.mcp_client import (
    BUILTIN_SERVER_NAMES,
    MCPConnectionError,
    ToolManager,
    test_configured_stdio_server,
)
from kylinguard.mcp_config import (
    MCP_TOOL_NAME_PATTERN,
    MCPConfigError,
    MCPConfigStore,
    redact_mcp_error,
)
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
    normalize_auto_review_root,
)
from kylinguard.pipeline import Confirmations, Pipeline, WorkspaceBusyError
from kylinguard.planner import Planner
from kylinguard.policy import KINDS, PolicyStore
from kylinguard.reviewer import Reviewer
from kylinguard.rules import builtin_rules
from kylinguard.alert_rules import AlertRuleStore
from kylinguard.alert_pusher import push_channel
from kylinguard.sessions import SessionStore
from kylinguard.snapshot import SnapshotCache
from kylinguard.skills import (
    MAX_SKILLS_PER_TURN,
    SkillConflictError,
    SkillDisabledError,
    SkillError,
    SkillNotFoundError,
    SkillStore,
    SkillValidationError,
    collect_skill_dependencies,
    normalize_selected_skill_ids,
)


_SYSTEM_AUDIT_SCOPES = {
    "__extensions__": "扩展配置",
    "__llm_config__": "模型配置",
    "__permissions__": "全局权限",
}
from kylinguard.storage_security import secure_database_path
from kylinguard.subprocess_env import safe_subprocess_env

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
_LOCAL_OPERATOR = "local"
logger = logging.getLogger(__name__)


class _ManagedStreamingResponse(StreamingResponse):
    """无论正文迭代是否开始，都执行一次流清理。"""

    def __init__(self, *args, cleanup, **kwargs):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            # StreamingResponse 会先发送响应头，再开始迭代正文。若客户端在
            # 两者之间断开，正文生成器的 finally 根本没有机会运行，因此
            # 还需要由整个 Response 生命周期兜底收口 worker 与审计终态。
            await self._cleanup()

ReasoningEffort = Literal[
    "auto", "none", "minimal", "low", "medium", "high", "xhigh", "max",
]
ProviderAdapter = Literal[
    "openai", "deepseek", "dashscope", "openai_compatible",
]


class SkillContextMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["skill"]
    offset: int = Field(ge=0, strict=True)
    skill_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$",
    )


class FileContextMention(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["file"]
    offset: int = Field(ge=0, strict=True)
    path: str = Field(min_length=1, max_length=4096)

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str) -> str:
        path = value.strip()
        if not path or "\x00" in path:
            raise ValueError("文件 mention 路径为空或包含 NUL")
        return path


ContextMention = Annotated[
    SkillContextMention | FileContextMention,
    Field(discriminator="type"),
]


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str
    session_id: str = ""  # 空 = 新建会话（SSE 首事件返回 session_created）
    request_id: str = Field(
        default="", max_length=128,
        pattern=r"^[A-Za-z0-9._:-]*$",
    )
    workspace_root: str = Field(default="", max_length=4096)
    provider_id: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=256)
    reasoning_effort: ReasoningEffort = "auto"
    skill_id: str = Field(
        default="", max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{0,63}$|^$",
    )
    skill_ids: list[str] = Field(
        default_factory=list, max_length=MAX_SKILLS_PER_TURN,
    )
    skill_mode: Literal["auto", "manual", "none"] = "auto"
    context_files: list[str] = Field(
        default_factory=list, max_length=MAX_CONTEXT_FILES,
    )
    context_mentions: list[ContextMention] = Field(
        default_factory=list, max_length=MAX_CONTEXT_MENTIONS,
    )

    @field_validator("context_files")
    @classmethod
    def validate_context_file_values(cls, values: list[str]) -> list[str]:
        for value in values:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("context_files 必须是非空相对路径字符串数组")
            if len(value) > 4096 or "\x00" in value:
                raise ValueError("context_files 路径过长或包含 NUL")
        return [value.strip() for value in values]

    @model_validator(mode="after")
    def validate_initial_permissions(self):
        try:
            normalized_skill_ids = normalize_selected_skill_ids(
                self.skill_ids, legacy_skill_id=self.skill_id,
            )
        except SkillValidationError as exc:
            raise ValueError(str(exc)) from exc
        self.skill_ids = list(normalized_skill_ids)
        if self.skill_mode == "none" and self.skill_ids:
            raise ValueError("skill_mode=none 时不能指定 skill_ids")
        if self.skill_mode == "manual" and not self.skill_ids:
            raise ValueError("skill_mode=manual 时必须指定 skill_ids")
        if self.skill_mode == "auto" and self.skill_ids:
            # 兼容旧版前端：显式 ID 永远等价于明确人工选择。
            self.skill_mode = "manual"
        selected_paths = set(self.context_files)
        selected_skill_ids = set(self.skill_ids)
        for mention in self.context_mentions:
            if mention.offset > len(self.message):
                raise ValueError(
                    "context_mentions.offset 超出 message 的 Unicode 字符范围"
                )
            if (isinstance(mention, SkillContextMention)
                    and mention.skill_id not in selected_skill_ids):
                raise ValueError(
                    "Skill mention 必须属于规范化 skill_ids"
                )
            if (isinstance(mention, FileContextMention)
                    and mention.path not in selected_paths):
                raise ValueError(
                    "文件 mention 必须属于规范化 context_files"
                )
        if bool(self.provider_id) != bool(self.model_id):
            raise ValueError("provider_id 与 model_id 必须同时提供")
        if not self.provider_id and self.reasoning_effort != "auto":
            raise ValueError("指定推理强度时必须同时指定模型")
        return self


class ConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


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


class LLMModelDiscoveryRequest(BaseModel):
    """使用尚未保存的提供商连接信息读取远端模型列表。"""

    adapter: ProviderAdapter
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr | None = None
    provider_id: str = Field(default="", max_length=64)
    version: int | None = Field(default=None, ge=1)
    allow_insecure_http: bool = False

    @field_validator("base_url")
    @classmethod
    def strip_base_url(cls, value: str) -> str:
        return value.strip()

    @field_validator("provider_id")
    @classmethod
    def strip_provider_id(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_connection_source(self):
        has_provider = bool(self.provider_id)
        has_version = self.version is not None
        if has_provider != has_version:
            raise ValueError("provider_id 与 version 必须同时提供")
        if has_provider and self.api_key is not None:
            raise ValueError("已有提供商不能同时提交 API Key")
        if not has_provider and self.api_key is None:
            raise ValueError("新连接必须提供 API Key")
        if (self.api_key is not None
                and not self.api_key.get_secret_value().strip()):
            raise ValueError("API Key 不能为空")
        return self


class PermissionUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: PermissionMode
    version: int = Field(ge=1)
    auto_review_roots: list[str] = Field(default_factory=list, max_length=32)


class FullAccessVisibilityUpdateRequest(BaseModel):
    visible: bool
    version: int = Field(ge=1)


class PermissionResolveRequest(BaseModel):
    decision: PermissionDecision
    context_version: int = Field(ge=1)
    authorized_path: str = ""


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


class MCPServerRequest(BaseModel):
    """自定义 stdio MCP 配置；secret_env 永远只写。"""

    id: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=80)
    command: str = Field(min_length=1, max_length=4096)
    cwd: str = Field(default="", max_length=4096)
    args: list[str] = Field(default_factory=list, max_length=128)
    env: dict[str, str] = Field(default_factory=dict, max_length=64)
    secret_env: dict[str, SecretStr] = Field(
        default_factory=dict, max_length=64,
    )
    clear_secret_env_keys: list[str] = Field(
        default_factory=list, max_length=64,
    )
    # 启停只能走独立接口；保留字段用于兼容前端草稿但不会据此启动程序。
    enabled: bool = False
    version: int | None = Field(default=None, ge=1)

    @field_validator("id", "name", "command", "cwd")
    @classmethod
    def strip_mcp_text(cls, value: str) -> str:
        return value.strip()

    def secret_values(self) -> dict[str, str]:
        return {
            key: value.get_secret_value()
            for key, value in self.secret_env.items()
        }


class MCPVersionRequest(BaseModel):
    version: int = Field(ge=1)


class MCPEnabledRequest(MCPVersionRequest):
    enabled: bool


class MCPToolPolicyValue(BaseModel):
    risk: Literal["low", "medium", "high"]
    definition_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


MCPToolPolicyName = Annotated[
    str, Field(min_length=1, max_length=128,
               pattern=rf"^{MCP_TOOL_NAME_PATTERN}$"),
]


class MCPToolPoliciesRequest(MCPVersionRequest):
    policies: dict[MCPToolPolicyName, MCPToolPolicyValue] = Field(
        default_factory=dict, max_length=256,
    )


class SkillRequest(BaseModel):
    id: str = Field(default="", max_length=64)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1024)
    version: str = Field(default="1.0.0", max_length=64)
    # KylinGuard 的可选兼容扩展：只检查依赖是否存在，不授权或限制工具。
    required_tools: list[str] = Field(default_factory=list, max_length=128)
    # 兼容旧客户端字段；自动路由上线后不再作为选择约束。
    manual_only: bool = False
    instructions: str = Field(min_length=1, max_length=128 * 1024)
    # 新建始终停用；编辑保持现有状态。字段只为兼容界面请求。
    enabled: bool = False
    expected_sha256: str = Field(
        default="", pattern=r"^(?:|[a-f0-9]{64})$",
    )

    @field_validator("id", "name", "description", "version", "instructions")
    @classmethod
    def strip_skill_text(cls, value: str) -> str:
        return value.strip()

class ExtensionEnabledRequest(BaseModel):
    enabled: bool
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_enabled: bool


class SkillVersionRequest(BaseModel):
    expected_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_enabled: bool


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
    data_root = Path(settings.db_path).expanduser().resolve().parent
    mcp_config = MCPConfigStore(
        settings.db_path,
        # 留空时由 MCPConfigStore 选择 XDG/用户状态目录。数据库在 WSL 的
        # /mnt/* 上时，旁目录无法可靠表达 0700/0600，不能用于存放凭据。
        secrets_dir=(settings.mcp_secrets_dir or None),
    )
    skills = SkillStore(
        user_dir=(settings.skills_dir or data_root / "skills"),
        state_path=(
            settings.skills_state_path or data_root / "skills-state.json"
        ),
    )
    # 将真正落盘路径回填到运行时 Settings，使结构化文件工具
    # 能把默认 XDG/数据库相对目录也视为控制面，而不只保护
    # 管理员显式写入环境变量的路径。
    settings.llm_secrets_dir = str(llm_config.secrets.directory)
    settings.mcp_secrets_dir = str(mcp_config.secrets.directory)
    settings.skills_dir = str(skills.user_dir)
    settings.skills_state_path = str(skills.state_path)
    current_execution_profile = execution_profile_fingerprint(settings)
    # 全局 FULL_ACCESS 从不跨后端进程重启继承。sudoers、附加组、capabilities
    # 或服务沙箱都可能在配置指纹未变化时扩大同一 UID 的权限。
    context = sessions.get_permission_settings()
    if context.mode == PermissionMode.FULL_ACCESS:
        revoke_reason = (
            "full_access_disabled"
            if not settings.allow_full_access
            else "service_restarted"
        )
        with audit.serialized():
            with sessions.transaction() as connection:
                fresh = sessions.get_permission_settings()
                changed = sessions.set_permission_settings(
                    mode=PermissionMode.ASK,
                    auto_review_roots=fresh.auto_review_roots,
                    expected_version=fresh.version,
                    updated_by="(server policy)",
                    execution_profile="",
                    commit=False,
                )
                audit.append(
                    "__permissions__",
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
    tools = ToolManager(
        exec_user=settings.exec_user,
        config_store=mcp_config,
        custom_call_timeout=settings.command_max_timeout,
        output_max_bytes=settings.output_max_bytes,
    )
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
        skills=skills,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        tools_started = False
        snapshot_started = False
        try:
            if with_tools:
                tools_started = True
                await tools.start()
                snapshot_started = True
                await snapshot_cache.start()
            yield
        finally:
            # 长寿命 MCP 子进程与 SQLite 连接必须逐项回收；
            # 某一项清理失败不能跳过后续资源。
            async def stop_safely(label: str, operation) -> None:
                try:
                    await operation()
                except (Exception, asyncio.CancelledError) as exc:
                    logger.error("停止 %s 失败：%s", label, exc)

            if snapshot_started:
                await stop_safely("快照缓存", snapshot_cache.stop)
            if tools_started:
                await stop_safely("MCP 工具管理器", tools.stop)
            for label, close in (
                ("MCP 配置库", mcp_config.close),
                ("模型配置库", llm_config.close),
                ("会话库", sessions.close),
                ("策略库", policies.close),
                ("告警规则库", alert_rule_store.close),
                ("审计库", audit.close),
            ):
                try:
                    close()
                except BaseException as exc:
                    logger.error("关闭 %s 失败：%s", label, exc)

    app = FastAPI(title="麒盾 KylinGuard", lifespan=lifespan)
    # 产品是无登录的本机单用户控制面。除端口绑定回环外，
    # 服务端也拒绝 DNS rebinding 携带的任意 Host。test/testserver
    # 仅供 ASGI 测试载体使用，真实套接字仍由部署层绑回环。
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
        "127.0.0.1", "localhost", "[::1]", "test", "testserver",
    ])

    @app.middleware("http")
    async def same_origin_mutations(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "").strip()
            host = request.headers.get("host", "").strip()
            if origin:
                from urllib.parse import urlsplit
                try:
                    origin_host = urlsplit(origin).netloc
                except ValueError:
                    origin_host = ""
                if not origin_host or origin_host.lower() != host.lower():
                    return JSONResponse(status_code=403, content={
                        "detail": {
                            "code": "cross_origin_mutation_denied",
                            "message": "本机控制面拒绝跨源状态修改请求。",
                        }
                    })
        return await call_next(request)

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
    app.state.mcp_config = mcp_config
    app.state.skills = skills
    app.state.tools = tools
    app.state.tools_active = with_tools

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

    def workspace_in_use(value: str, *, exclude_session: str = "") -> bool:
        checker = getattr(app.state.pipeline, "workspace_in_use", None)
        return bool(
            callable(checker)
            and checker(value, exclude_session=exclude_session)
        )

    def workspace_busy_error(value: str) -> HTTPException:
        return HTTPException(409, detail={
            "code": "workspace_busy",
            "message": "该工作目录正在被另一个任务使用，当前对话仅可查看。",
            "workspace_root": value,
            "retryable": True,
        })

    async def claim_workspace(value: str, session_id: str) -> str:
        """优先使用 Pipeline 的原子占位；兼容测试或外部注入的旧实现。"""
        claimer = getattr(app.state.pipeline, "claim_workspace", None)
        if not callable(claimer):
            if workspace_in_use(value):
                raise workspace_busy_error(value)
            return ""
        try:
            token = await claimer(value, session_id)
        except WorkspaceBusyError as exc:
            raise workspace_busy_error(value) from exc
        return str(token or "")

    async def release_workspace_claim(token: str) -> None:
        if not token:
            return
        releaser = getattr(app.state.pipeline, "release_workspace_claim", None)
        if callable(releaser):
            await releaser(token)

    def permission_payload(context) -> dict:
        payload = {
            **context.model_dump(mode="json"),
            **permission_capabilities_payload(),
        }
        payload["workspace_root"] = (
            app.state.sessions.get_workspace_root(context.session_id)
            if context.session_id else settings.workspace_root
        ) or settings.workspace_root
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

    def audit_extension(event_type: str, user: str, **payload) -> None:
        """扩展审计只记录元数据；不接收环境变量值或 Skill 正文。"""
        app.state.audit.append("__extensions__", event_type, {
            "operator": user,
            **payload,
        })

    def mcp_http_error(error: MCPConfigError) -> HTTPException:
        return HTTPException(
            error.status_code,
            detail={"code": error.code, "message": error.message},
        )

    def skill_http_error(error: SkillError) -> HTTPException:
        if isinstance(error, SkillNotFoundError):
            status = 404
            code = "skill_not_found"
        elif isinstance(error, SkillDisabledError):
            status = 409
            code = "skill_disabled"
        elif isinstance(error, SkillConflictError):
            status = 409
            code = "skill_conflict"
        elif isinstance(error, SkillValidationError):
            status = 400
            code = "skill_invalid"
        else:
            status = 400
            code = "skill_error"
        return HTTPException(
            status,
            detail={"code": code, "message": str(error)},
        )

    def render_skill_document(req: SkillRequest) -> str:
        """把结构化表单安全序列化成受限 SKILL.md。"""
        def scalar(value: str) -> str:
            return json.dumps(value, ensure_ascii=False)

        lines = [
            "---",
            f"name: {scalar(req.name)}",
            f"description: {scalar(req.description or req.name)}",
            f"version: {scalar(req.version or '1.0.0')}",
            "required_tools:",
            *[f"  - {scalar(tool)}" for tool in req.required_tools],
            # 创建或编辑正文不会暗中启用 Skill；显式启停状态由状态文件覆盖。
            "enabled: false",
            "---",
            req.instructions,
            "",
        ]
        return "\n".join(lines)

    def skill_payload(summary) -> dict:
        definition = app.state.skills.get_skill(
            summary.id, include_disabled=True,
        )
        has_tool = getattr(app.state.tools, "has_tool", None)
        missing_tools = [
            tool for tool in definition.required_tools
            if not (callable(has_tool) and has_tool(tool))
        ]
        return {
            **summary.model_dump(mode="json"),
            "instructions": definition.instructions,
            "missing_tools": missing_tools,
            "available": summary.enabled and not missing_tools,
        }

    async def extensions_payload() -> dict:
        summaries = app.state.skills.list_skills()
        configured_mcp = app.state.mcp_config.list_servers()
        active_server_summaries = getattr(
            app.state.tools, "active_server_summaries", None,
        )
        active_mcp = (
            await active_server_summaries()
            if app.state.tools_active and callable(active_server_summaries)
            else []
        )
        active_by_id = {item["id"]: item for item in active_mcp}
        enabled_mcp_servers = [
            {
                **item,
                "name": BUILTIN_SERVER_NAMES.get(item["id"], item["id"]),
                "available": True,
            }
            for item in active_mcp
            if item["source"] == "builtin"
        ]
        enabled_mcp_servers.extend(
            {
                "id": item["id"],
                "name": item["name"],
                "source": "custom",
                "tool_count": active_by_id.get(
                    item["id"], {"tool_count": item.get("tool_count", 0)},
                )["tool_count"],
                "available": item["id"] in active_by_id,
            }
            for item in configured_mcp
            if item["enabled"]
        )
        return {
            "mcp_servers": configured_mcp,
            "enabled_mcp_servers": enabled_mcp_servers,
            "skills": [skill_payload(item) for item in summaries],
            "skill_issues": [
                issue.model_dump(mode="json")
                for issue in app.state.skills.issues()
            ],
        }

    async def reload_custom_tools() -> dict:
        if not app.state.tools_active:
            return {"loaded": [], "failed": {}, "disabled": []}
        return await app.state.tools.reload_custom()

    async def detach_custom_tool(server_id: str) -> dict:
        """先从工具路由摘除服务；子进程回收由 ToolManager 后台完成。"""
        if not app.state.tools_active:
            return {"detached": False, "inactive": True}
        detach = getattr(app.state.tools, "detach_custom", None)
        if not callable(detach):
            return {"detached": False, "fallback_reload": True}
        # detach 在返回前已完成路由摘除；shield 保证 HTTP 请求取消也不会
        # 中断这一 fail-closed 收敛动作。
        return {"detached": bool(await asyncio.shield(detach(server_id)))}

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

    def transactional_extension_audit(
        event_type: str, user: str, payload_builder,
    ):
        """把 MCP 配置终态与扩展审计写入同一个 SQLite 事务。"""
        def append(value, connection):
            app.state.audit.append(
                "__extensions__",
                event_type,
                {"operator": user, **payload_builder(value)},
                connection=connection,
                commit=False,
                lock_held=True,
            )
        return append

    def mcp_audit_payload(server: dict) -> dict:
        """MCP 审计白名单；绝不包含 secret_env 值。"""
        return {
            "server_id": server["id"],
            "name": server["name"],
            "version": server["version"],
            "command": server["command"],
            "cwd": server.get("cwd", ""),
            "arg_count": len(server.get("args", [])),
            "env_keys": sorted(server.get("env", {})),
            "secret_env_keys": server.get("secret_env_keys", []),
            "enabled": bool(server.get("enabled")),
            "tools": [
                tool.get("name", "")
                for tool in server.get("tools", [])
                if isinstance(tool, dict) and tool.get("name")
            ],
            "tool_risks": {
                name: policy.get("risk", "high")
                for name, policy in server.get("tool_policies", {}).items()
                if isinstance(policy, dict)
            },
            "tool_policies": {
                name: {
                    "risk": policy.get("risk", "high"),
                    "definition_sha256": policy.get("definition_sha256", ""),
                }
                for name, policy in server.get("tool_policies", {}).items()
                if isinstance(policy, dict)
            },
        }

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/context/files")
    async def context_file_candidates(
        q: str = Query(default="", max_length=200),
        root: str = Query(default="", max_length=4096),
        _user: str = Depends(local_operator),
    ):
        """搜索已知工作目录中的文件路径；绝不读取文件内容。"""
        try:
            requested_root = resolve_session_workspace(root)
        except SessionPermissionError as exc:
            raise permission_http_error(exc) from exc
        try:
            result = search_context_files(requested_root, q)
        except ContextFileError as exc:
            raise HTTPException(400, detail={
                "code": "context_search_invalid",
                "message": str(exc),
            }) from exc
        result["files"] = [
            {**item, "path": item["relative_path"]}
            for item in result["files"]
        ]
        return result

    # ---- MCP 与 Skill 扩展 ----

    @app.get("/api/extensions")
    async def get_extensions(_user: str = Depends(local_operator)):
        try:
            return await extensions_payload()
        except SkillError as exc:
            raise skill_http_error(exc) from exc

    @app.post("/api/extensions/mcp", status_code=201)
    async def create_mcp_server(
        req: MCPServerRequest, user: str = Depends(local_operator),
    ):
        if not req.id:
            raise HTTPException(422, detail={
                "code": "mcp_server_id_required",
                "message": "添加 MCP 服务必须提供 ID。",
            })
        audit_extension(
            "mcp_server_create_requested", user,
            server_id=req.id, name=req.name, command=req.command,
            cwd=req.cwd,
            arg_count=len(req.args), env_keys=sorted(req.env),
            secret_env_keys=sorted(req.secret_env),
        )
        try:
            with app.state.audit.serialized():
                server = app.state.mcp_config.create_server(
                    server_id=req.id,
                    name=req.name,
                    command=req.command,
                    cwd=req.cwd or None,
                    args=req.args,
                    env=req.env,
                    secret_env=req.secret_values(),
                    # 保存与启动严格分离，避免一次普通表单提交执行新程序。
                    enabled=False,
                    updated_by=user,
                    audit=transactional_extension_audit(
                        "mcp_server_created", user, mcp_audit_payload,
                    ),
                )
        except MCPConfigError as exc:
            raise mcp_http_error(exc) from exc
        return {"server": server}

    @app.put("/api/extensions/mcp/{server_id}")
    async def update_mcp_server(
        server_id: str, req: MCPServerRequest,
        user: str = Depends(local_operator),
    ):
        if req.version is None:
            raise HTTPException(422, detail={
                "code": "mcp_version_required",
                "message": "编辑 MCP 服务必须携带当前版本。",
            })
        try:
            current = app.state.mcp_config.get_server(server_id)
            if current["enabled"]:
                raise MCPConfigError(
                    "mcp_disable_before_edit",
                    "请先停用 MCP 服务，再修改启动配置。",
                    status_code=409,
                )
            audit_extension(
                "mcp_server_update_requested", user,
                server_id=server_id, expected_version=req.version,
                name=req.name, command=req.command, cwd=req.cwd,
                arg_count=len(req.args), env_keys=sorted(req.env),
                secret_env_keys=sorted(req.secret_env),
                clear_secret_env_keys=sorted(req.clear_secret_env_keys),
            )
            with app.state.audit.serialized():
                server = app.state.mcp_config.update_server(
                    server_id,
                    expected_version=req.version,
                    name=req.name,
                    command=req.command,
                    cwd=req.cwd or None,
                    args=req.args,
                    env=req.env,
                    secret_env=req.secret_values(),
                    clear_secret_env_keys=req.clear_secret_env_keys,
                    enabled=False,
                    updated_by=user,
                    audit=transactional_extension_audit(
                        "mcp_server_updated", user, mcp_audit_payload,
                    ),
                )
        except MCPConfigError as exc:
            raise mcp_http_error(exc) from exc
        return {"server": server}

    @app.post("/api/extensions/mcp/{server_id}/test")
    async def test_mcp_server(
        server_id: str, req: MCPVersionRequest,
        user: str = Depends(local_operator),
    ):
        try:
            current = app.state.mcp_config.get_server(server_id)
            if current["version"] != req.version:
                raise MCPConfigError(
                    "mcp_config_version_conflict",
                    "MCP 服务配置已变化，请刷新后重试。",
                    status_code=409,
                )
            # 测试会启动第三方代码；先落审计意图，审计不可用时
            # 绝不启动程序。
            audit_extension(
                "mcp_server_test_requested", user,
                server_id=server_id, version=req.version,
                command=current["command"],
            )
            result = await test_configured_stdio_server(
                app.state.mcp_config, server_id,
                exec_user=settings.exec_user,
                expected_version=req.version,
            )
            runtime = None
            fresh = app.state.mcp_config.get_server(server_id)
            if fresh["version"] == req.version and fresh["enabled"]:
                runtime = await reload_custom_tools()
                if server_id in runtime.get("failed", {}):
                    raise MCPConfigError(
                        "mcp_runtime_unavailable",
                        ("MCP 临时测试成功，但运行时恢复失败："
                         + runtime["failed"][server_id]),
                        status_code=400,
                    )
        except MCPConfigError as exc:
            raise mcp_http_error(exc) from exc
        except MCPConnectionError as exc:
            audit_extension(
                "mcp_server_tested", user, server_id=server_id,
                version=req.version, ok=False, error=exc.message,
            )
            raise HTTPException(400, detail={
                "code": "mcp_connection_failed",
                "message": exc.message,
            }) from exc
        audit_extension(
            "mcp_server_tested", user, server_id=server_id,
            version=req.version, ok=True,
            tool_count=result["tool_count"],
            tools=[tool["name"] for tool in result["tools"]],
        )
        return {**result, "runtime": runtime}

    @app.put("/api/extensions/mcp/{server_id}/tool-policies")
    async def set_mcp_tool_policies(
        server_id: str, req: MCPToolPoliciesRequest,
        user: str = Depends(local_operator),
    ):
        policies = {
            name: value.model_dump(mode="json")
            for name, value in req.policies.items()
        }
        audit_extension(
            "mcp_tool_policies_update_requested", user,
            server_id=server_id,
            expected_version=req.version,
            tool_risks={name: value["risk"]
                        for name, value in policies.items()},
            tool_policies=policies,
        )
        try:
            with app.state.audit.serialized():
                server = app.state.mcp_config.set_tool_policies(
                    server_id,
                    expected_version=req.version,
                    policies=policies,
                    updated_by=user,
                    audit=transactional_extension_audit(
                        "mcp_tool_policies_updated", user, mcp_audit_payload,
                    ),
                )
        except MCPConfigError as exc:
            raise mcp_http_error(exc) from exc
        return {"server": server}

    @app.post("/api/extensions/mcp/{server_id}/enabled")
    async def set_mcp_server_enabled(
        server_id: str, req: MCPEnabledRequest,
        user: str = Depends(local_operator),
    ):
        try:
            current = app.state.mcp_config.get_server(server_id)
            if current["version"] != req.version:
                raise MCPConfigError(
                    "mcp_config_version_conflict",
                    "MCP 服务配置已变化，请刷新后重试。",
                    status_code=409,
                )
            if current["enabled"] == req.enabled:
                return {"server": current, "runtime": None}

            audit_extension(
                "mcp_server_enable_requested" if req.enabled
                else "mcp_server_disable_requested",
                user, server_id=server_id, version=req.version,
                command=current["command"],
            )

            if req.enabled:
                # 启用是代码执行边界：先临时握手并发现工具，成功后才持久化。
                await test_configured_stdio_server(
                    app.state.mcp_config, server_id,
                    exec_user=settings.exec_user,
                    expected_version=req.version,
                )
                with app.state.audit.serialized():
                    server = app.state.mcp_config.set_enabled(
                        server_id,
                        expected_version=req.version,
                        enabled=True,
                        updated_by=user,
                        audit=transactional_extension_audit(
                            "mcp_server_enabled", user, mcp_audit_payload,
                        ),
                    )
                try:
                    runtime = await reload_custom_tools()
                    failure = runtime.get("failed", {}).get(server_id, "")
                    if failure:
                        raise MCPConfigError(
                            "mcp_start_failed", failure, status_code=400,
                        )
                except BaseException as exc:
                    # 无论请求取消还是启动失败，都先让该服务不可再被新调用。
                    await detach_custom_tool(server_id)
                    safe_error = (
                        exc.message if isinstance(exc, MCPConfigError)
                        else redact_mcp_error(exc)
                    )
                    try:
                        with app.state.audit.serialized():
                            rolled_back = app.state.mcp_config.set_enabled(
                                server_id,
                                expected_version=server["version"],
                                enabled=False,
                                updated_by="(automatic rollback)",
                                audit=transactional_extension_audit(
                                    "mcp_server_enable_failed", user,
                                    lambda value: {
                                        **mcp_audit_payload(value),
                                        "error": safe_error,
                                    },
                                ),
                            )
                    except Exception:
                        # 终态审计不可用时仍优先收紧持久化开关，避免重启后
                        # 重新执行已失败的第三方程序。
                        rolled_back = app.state.mcp_config.set_enabled(
                            server_id,
                            expected_version=server["version"],
                            enabled=False,
                            updated_by="(automatic safety rollback)",
                        )
                    if isinstance(exc, asyncio.CancelledError):
                        raise
                    raise MCPConfigError(
                        "mcp_start_failed",
                        f"MCP 服务启动失败，已恢复为停用状态：{safe_error}",
                        status_code=400,
                    ) from exc
            else:
                # 停用先摘路由、后提交状态与终态审计；任何中途失败都保持
                # fail closed，不会出现界面显示停用但 Agent 仍可新调用。
                runtime = await detach_custom_tool(server_id)
                if runtime.get("fallback_reload"):
                    raise MCPConfigError(
                        "mcp_runtime_detach_unavailable",
                        "当前工具运行时不支持安全停用，请重启服务后重试。",
                        status_code=500,
                    )
                with app.state.audit.serialized():
                    server = app.state.mcp_config.set_enabled(
                        server_id,
                        expected_version=req.version,
                        enabled=False,
                        updated_by=user,
                        audit=transactional_extension_audit(
                            "mcp_server_disabled", user, mcp_audit_payload,
                        ),
                    )
        except MCPConfigError as exc:
            raise mcp_http_error(exc) from exc
        except MCPConnectionError as exc:
            audit_extension(
                "mcp_server_enable_failed", user,
                server_id=server_id, version=req.version, error=exc.message,
            )
            raise HTTPException(400, detail={
                "code": "mcp_connection_failed",
                "message": exc.message,
            }) from exc
        return {"server": server, "runtime": runtime}

    @app.delete("/api/extensions/mcp/{server_id}")
    async def delete_mcp_server(
        server_id: str, req: MCPVersionRequest,
        user: str = Depends(local_operator),
    ):
        try:
            current = app.state.mcp_config.get_server(server_id)
            if current["version"] != req.version:
                raise MCPConfigError(
                    "mcp_config_version_conflict",
                    "MCP 服务配置已变化，请刷新后重试。",
                    status_code=409,
                )
            audit_extension(
                "mcp_server_delete_requested", user,
                server_id=server_id, version=req.version,
                name=current["name"], enabled=current["enabled"],
            )
            runtime = await detach_custom_tool(server_id)
            if runtime.get("fallback_reload"):
                raise MCPConfigError(
                    "mcp_runtime_detach_unavailable",
                    "当前工具运行时不支持安全删除，请重启服务后重试。",
                    status_code=500,
                )
            with app.state.audit.serialized():
                app.state.mcp_config.delete_server(
                    server_id,
                    expected_version=req.version,
                    audit=transactional_extension_audit(
                        "mcp_server_deleted", user, mcp_audit_payload,
                    ),
                )
        except MCPConfigError as exc:
            raise mcp_http_error(exc) from exc
        return {"ok": True, "runtime": runtime}

    @app.post("/api/extensions/skills", status_code=201)
    async def create_skill(
        req: SkillRequest, user: str = Depends(local_operator),
    ):
        if not req.id:
            raise HTTPException(422, detail={
                "code": "skill_id_required",
                "message": "添加 Skill 必须提供 ID。",
            })
        audit_extension(
            "skill_create_requested", user,
            skill_id=req.id, name=req.name, version=req.version,
            required_tools=req.required_tools,
        )
        try:
            definition = app.state.skills.create_user_skill(
                req.id, render_skill_document(req),
            )
        except SkillError as exc:
            raise skill_http_error(exc) from exc
        try:
            audit_extension(
                "skill_created", user, **definition.audit_payload(),
            )
        except Exception:
            # Skill 文件与审计库无法共享事务；终态审计失败时恢复到
            # 请求前的“不存在”状态，避免接口 500 却留下幽灵配置。
            try:
                app.state.skills.delete_user_skill(
                    req.id,
                    expected_sha256=definition.sha256,
                    expected_enabled=definition.enabled,
                )
            except Exception:
                logger.exception("Skill 创建审计失败后的回滚也失败: %s", req.id)
            raise
        return {"skill": skill_payload(definition)}

    @app.put("/api/extensions/skills/{skill_id}")
    async def update_skill(
        skill_id: str, req: SkillRequest,
        user: str = Depends(local_operator),
    ):
        try:
            if not req.expected_sha256:
                raise SkillValidationError("编辑 Skill 必须携带当前内容哈希。")
            previous = app.state.skills.get_skill(
                skill_id, include_disabled=True,
            )
            if previous.sha256 != req.expected_sha256:
                raise SkillConflictError(
                    "Skill 内容已被其他操作修改，请刷新后重试。"
                )
            audit_extension(
                "skill_update_requested", user,
                skill_id=skill_id, expected_sha256=req.expected_sha256,
                name=req.name, version=req.version,
                required_tools=req.required_tools,
            )
            definition = app.state.skills.update_user_skill(
                skill_id, render_skill_document(req),
                expected_sha256=req.expected_sha256,
            )
        except SkillError as exc:
            raise skill_http_error(exc) from exc
        try:
            audit_extension(
                "skill_updated", user, **definition.audit_payload(),
            )
        except Exception:
            try:
                app.state.skills.update_user_skill(
                    skill_id, previous.content,
                    expected_sha256=definition.sha256,
                )
            except Exception:
                logger.exception("Skill 更新审计失败后的回滚也失败: %s", skill_id)
            raise
        return {"skill": skill_payload(definition)}

    @app.post("/api/extensions/skills/{skill_id}/enabled")
    async def set_skill_enabled(
        skill_id: str, req: ExtensionEnabledRequest,
        user: str = Depends(local_operator),
    ):
        try:
            previous = app.state.skills.get_skill(
                skill_id, include_disabled=True,
            )
            if (previous.sha256 != req.expected_sha256
                    or previous.enabled is not req.expected_enabled):
                raise SkillConflictError(
                    "Skill 已被其他操作修改，请刷新后重试。"
                )
            audit_extension(
                "skill_enable_requested" if req.enabled
                else "skill_disable_requested",
                user, skill_id=skill_id,
                expected_sha256=req.expected_sha256,
                expected_enabled=req.expected_enabled,
            )
            definition = app.state.skills.set_enabled(
                skill_id, req.enabled,
                expected_sha256=req.expected_sha256,
                expected_enabled=req.expected_enabled,
            )
        except SkillError as exc:
            raise skill_http_error(exc) from exc
        try:
            audit_extension(
                "skill_enabled" if req.enabled else "skill_disabled",
                user, **definition.audit_payload(),
            )
        except Exception:
            try:
                app.state.skills.set_enabled(
                    skill_id, previous.enabled,
                    expected_sha256=definition.sha256,
                    expected_enabled=definition.enabled,
                )
            except Exception:
                logger.exception("Skill 启停审计失败后的回滚也失败: %s", skill_id)
            raise
        return {"skill": skill_payload(definition)}

    @app.delete("/api/extensions/skills/{skill_id}")
    async def delete_skill(
        skill_id: str, req: SkillVersionRequest,
        user: str = Depends(local_operator),
    ):
        try:
            definition = app.state.skills.get_skill(
                skill_id, include_disabled=True,
            )
            if (definition.sha256 != req.expected_sha256
                    or definition.enabled is not req.expected_enabled):
                raise SkillConflictError(
                    "Skill 已被其他操作修改，请刷新后重试。"
                )
            audit_extension(
                "skill_delete_requested", user,
                **definition.audit_payload(),
            )
            app.state.skills.delete_user_skill(
                skill_id,
                expected_sha256=req.expected_sha256,
                expected_enabled=req.expected_enabled,
            )
        except SkillError as exc:
            raise skill_http_error(exc) from exc
        try:
            audit_extension(
                "skill_deleted", user, **definition.audit_payload(),
            )
        except Exception:
            try:
                restored = app.state.skills.create_user_skill(
                    skill_id, definition.content,
                )
                if restored.enabled is not definition.enabled:
                    app.state.skills.set_enabled(
                        skill_id, definition.enabled,
                        expected_sha256=restored.sha256,
                        expected_enabled=restored.enabled,
                    )
            except Exception:
                logger.exception("Skill 删除审计失败后的回滚也失败: %s", skill_id)
            raise
        return {"ok": True}

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

    @app.post("/api/llm/discover-models")
    async def discover_draft_llm_models(
        req: LLMModelDiscoveryRequest, _user: str = Depends(local_operator),
    ):
        """不保存表单或 API Key，直接读取当前连接可用的模型 ID。"""
        try:
            if req.provider_id:
                assert req.version is not None
                ids = await app.state.llm_runtime.fetch_model_ids_for_provider_draft(
                    provider_id=req.provider_id,
                    expected_version=req.version,
                    adapter=req.adapter,
                    base_url=req.base_url,
                    allow_insecure_http=req.allow_insecure_http,
                )
            else:
                assert req.api_key is not None
                ids = await app.state.llm_runtime.fetch_model_ids_for_connection(
                    adapter=req.adapter,
                    base_url=req.base_url,
                    api_key=req.api_key.get_secret_value(),
                    allow_insecure_http=req.allow_insecure_http,
                )
        except Exception as exc:
            raise provider_action_error(exc) from exc
        return {"models": ids}

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
        selected_skills = ()
        combined_required_tools: tuple[str, ...] = ()
        if req.skill_ids:
            try:
                # 在创建会话前原子校验整组，避免部分有效的选择留下空会话；
                # Pipeline 仍会在实际开始该轮时重新冻结权威快照。
                selected_skills = app.state.skills.get_skills(
                    tuple(req.skill_ids),
                )
                combined_required_tools = collect_skill_dependencies(
                    selected_skills,
                )
            except SkillError as exc:
                raise skill_http_error(exc) from exc
            has_tool = getattr(app.state.tools, "has_tool", None)
            missing_tools = [
                tool for tool in combined_required_tools
                if not (callable(has_tool) and has_tool(tool))
            ]
            if missing_tools:
                raise HTTPException(409, detail={
                    "code": "skill_required_tools_missing",
                    "message": (
                        "所选 Skill 缺少依赖工具："
                        + "、".join(missing_tools)
                    ),
                    "missing_tools": missing_tools,
                })
        skill_names = {item.id: item.name for item in selected_skills}
        raw_context_mentions = [
            item.model_dump(mode="json") for item in req.context_mentions
        ]

        def resolved_mentions(files: list[dict]) -> list[dict]:
            return normalize_context_mentions(
                req.message,
                raw_context_mentions,
                skill_names=skill_names,
                context_files=files,
            )

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
        resolved_context_files: list[dict] = []
        resolved_context_mentions: list[dict] = []
        workspace_claim = ""
        if created:
            session_id = uuid.uuid4().hex
            try:
                workspace_root = resolve_session_workspace(req.workspace_root)
                resolved_context_files = validate_context_files(
                    workspace_root, req.context_files,
                )
                resolved_context_mentions = resolved_mentions(
                    resolved_context_files,
                )
                workspace_claim = await claim_workspace(
                    workspace_root, session_id,
                )
                # 会话只保存工作目录和模型；审批模式及自动执行范围来自全局设置。
                try:
                    with app.state.sessions.transaction() as connection:
                        app.state.sessions.create(
                            session_id, req.message,
                            workspace_root=workspace_root, commit=False,
                        )
                        session_model = (
                            app.state.llm_config.create_session_with_connection(
                                connection, session_id, requested_model,
                                updated_by=user,
                            )
                        )
                except BaseException:
                    await release_workspace_claim(workspace_claim)
                    workspace_claim = ""
                    raise
            except SessionPermissionError as exc:
                raise permission_http_error(exc) from exc
            except ContextMentionError as exc:
                raise HTTPException(400, detail={
                    "code": "context_mentions_invalid",
                    "message": str(exc),
                }) from exc
            except ContextFileError as exc:
                raise HTTPException(400, detail={
                    "code": "context_files_invalid",
                    "message": str(exc),
                }) from exc
            except LLMConfigError as exc:
                raise llm_http_error(exc) from exc
        else:
            session_id = req.session_id
            if not app.state.sessions.exists(session_id):
                raise HTTPException(404, "会话不存在")
            is_busy = getattr(
                app.state.pipeline, "session_busy", lambda _id: False,
            )
            if is_busy(session_id):
                raise HTTPException(409, detail={
                    "code": "session_busy",
                    "message": "当前对话已有任务正在运行，请等待完成后再发送。",
                    "retryable": True,
                })
            if (req.workspace_root or req.provider_id or req.model_id
                    or req.reasoning_effort != "auto"):
                raise HTTPException(
                    400,
                    detail={
                        "code": "permission_update_requires_endpoint",
                        "message": "已有会话请通过专用接口更新工作目录或模型。",
                    },
                )
            try:
                session_model = app.state.llm_config.ensure_session(
                    session_id, updated_by=user)
            except LLMConfigError as exc:
                raise llm_http_error(exc) from exc
            try:
                workspace_root = (
                    app.state.sessions.get_workspace_root(session_id)
                    or settings.workspace_root
                )
                resolved_context_files = validate_context_files(
                    workspace_root, req.context_files,
                )
                resolved_context_mentions = resolved_mentions(
                    resolved_context_files,
                )
            except ContextMentionError as exc:
                raise HTTPException(400, detail={
                    "code": "context_mentions_invalid",
                    "message": str(exc),
                }) from exc
            except ContextFileError as exc:
                raise HTTPException(400, detail={
                    "code": "context_files_invalid",
                    "message": str(exc),
                }) from exc
            workspace_claim = await claim_workspace(workspace_root, session_id)
            try:
                app.state.sessions.touch(session_id, first_message=req.message)
            except BaseException:
                await release_workspace_claim(workspace_claim)
                workspace_claim = ""
                raise
        queue: asyncio.Queue = asyncio.Queue()
        last_progress: dict = {}
        pending_confirm: dict | None = None
        started = time.monotonic()
        cancellation_recorded = False
        terminal_enqueued = False

        def refresh_pipeline_context() -> None:
            """API 补写终态后同步刷新同进程模型上下文缓存。"""
            refresh = getattr(
                app.state.pipeline, "refresh_session_context", None,
            )
            if callable(refresh):
                refresh(session_id)

        async def emit(event: dict):
            nonlocal last_progress, pending_confirm, terminal_enqueued
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
            if event.get("type") in {"final_answer", "fatal"}:
                # Pipeline 在 emit 前已将 final_answer 写入审计。即使客户端
                # 尚未读取 done，此时业务结果也已确定，断流不能再改写成取消。
                terminal_enqueued = True
            # 对 worker 施加真实的 SSE 发送背压。否则连续事件会在无界队列
            # 中瞬间堆积，客户端恰在 ``yield`` 边界断开时，ASGI 可能先把
            # 请求取消返回、稍后才关闭生成器，导致取消审计出现短暂空窗。
            delivered = asyncio.get_running_loop().create_future()
            await queue.put((event, delivered))
            await delivered

        def record_client_cancellation() -> None:
            """同步、幂等地收口断流终态。

            StreamingResponse/ASGI 可能在异步生成器停在 ``yield`` 时直接
            关闭它；此时仅依赖后台 worker 收到 CancelledError 会形成竞态。
            生成器和 worker 共用该函数，先标记再写入，确保最多记录一次。
            """
            nonlocal cancellation_recorded
            if cancellation_recorded or terminal_enqueued:
                return
            cancellation_recorded = True
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
                refresh_pipeline_context()
            except AuditError:
                pass

        async def run():
            nonlocal terminal_enqueued
            try:
                kwargs = {}
                if req.skill_ids:
                    kwargs["skill_ids"] = list(req.skill_ids)
                    kwargs["skill_mode"] = req.skill_mode
                elif req.skill_mode != "auto":
                    kwargs["skill_mode"] = req.skill_mode
                if resolved_context_files:
                    kwargs["context_files"] = [
                        item["relative_path"]
                        for item in resolved_context_files
                    ]
                if resolved_context_mentions:
                    kwargs["context_mentions"] = resolved_context_mentions
                if workspace_claim:
                    kwargs["workspace_claim"] = workspace_claim
                await app.state.pipeline.handle(
                    session_id, req.message, emit, **kwargs,
                )
            except asyncio.CancelledError:
                # SSE 断开会停止本轮流水线；即使客户端已收不到事件，
                # 也必须在审计中留下明确终态，避免历史会话看似悬空。
                record_client_cancellation()
                raise
            except AuditError:
                terminal_enqueued = True
                await queue.put({"type": "fatal",
                                 "error": "审计写入失败，任务已中止。",
                                 "request_id": req.request_id})
            except Exception as exc:  # 未知错误也必须形成可回放的安全终态
                if isinstance(exc, WorkspaceBusyError):
                    error = public_error(
                        "workspace_busy",
                        "该工作目录正在被另一个任务使用，当前对话仅可查看。",
                        retryable=True,
                    )
                elif isinstance(exc, LLMError):
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
                    refresh_pipeline_context()
                    terminal_enqueued = True
                    await queue.put({
                        "type": "final_answer",
                        "session_id": session_id,
                        "hash": h,
                        **final_payload,
                    })
                except AuditError:
                    terminal_enqueued = True
                    await queue.put({
                        "type": "fatal",
                        "error": "审计写入失败，任务已中止。",
                        "request_id": req.request_id,
                    })
            finally:
                await release_workspace_claim(workspace_claim)
                await queue.put(None)

        # worker 必须在 Response 返回前启动，并至少产出一个事件。否则
        # StreamingResponse 已发出 X-Session-Id、但尚未开始迭代正文时若断流，
        # 本轮原始指令不会进入审计，下一次“继续”仍然无从恢复目标。
        try:
            task = asyncio.create_task(run())
        except BaseException:
            await release_workspace_claim(workspace_claim)
            raise
        reached_end = False
        cleanup_finished = False

        async def cleanup_stream() -> None:
            """幂等收口 worker；由正文生成器与 Response 生命周期共用。"""
            nonlocal cleanup_finished
            if cleanup_finished:
                return
            cleanup_finished = True
            if (not reached_end and not terminal_enqueued
                    and not task.done()):
                # 先同步记录终态，再取消 worker。这样即使 ASGI 在响应头或
                # yield 边界中断，调用方返回时审计也已经收口。
                record_client_cancellation()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except RuntimeError as exc:
                # Python/ASGI 在异步生成器 athrow 清理边界上可能报告
                # “cannot reuse already awaited coroutine”。worker 已取消且
                # 终态已同步记录，该清理异常不应形成未取回 Task。
                if "cannot reuse already awaited coroutine" not in str(exc):
                    raise

        try:
            first_item = await queue.get()
        except BaseException:
            await cleanup_stream()
            raise

        async def stream():
            nonlocal reached_end
            try:
                if created:
                    # 首个流水线事件已缓存在 first_item 中；session_created
                    # 仍保持为新会话对外可见的第一个 SSE 事件。
                    yield ("data: " + json.dumps(
                        {"type": "session_created", "session_id": session_id,
                         "request_id": req.request_id,
                         "model_context": session_model},
                        ensure_ascii=False) + "\n\n")
                item = first_item
                while True:
                    if item is None:
                        reached_end = True
                        break
                    if isinstance(item, tuple):
                        event, delivered = item
                    else:
                        event, delivered = item, None
                    if (req.request_id
                            and event.get("type") in {"task_error", "fatal"}):
                        event = {**event, "request_id": req.request_id}
                    try:
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    finally:
                        if delivered is not None and not delivered.done():
                            delivered.set_result(None)
                    item = await queue.get()
                yield 'data: {"type": "done"}\n\n'
            finally:
                await cleanup_stream()

        return _ManagedStreamingResponse(
            stream(),
            cleanup=cleanup_stream,
            media_type="text/event-stream",
            headers={
                # 前端在读取第一个 SSE 事件前即可绑定服务端已经创建的会话；
                # 即使首事件前断流，下一次“继续”也不会意外新建第二个会话。
                "X-Session-Id": session_id,
                "Access-Control-Expose-Headers": "X-Session-Id",
            },
        )

    @app.post("/api/confirm")
    async def confirm(req: ConfirmRequest,
                      user: str = Depends(local_operator)):
        # operator 随 confirm_result 写入审计链：确认决断归因到管理员账号
        return {"ok": app.state.confirmations.resolve(
            req.confirm_id, req.approved, operator=user)}

    @app.get("/api/sessions")
    async def list_sessions(_user: str = Depends(local_operator)):
        summaries = app.state.sessions.list(include_drafts=False)
        is_busy = getattr(app.state.pipeline, "session_busy", lambda _id: False)
        workspace_busy = getattr(
            app.state.pipeline, "workspace_in_use", lambda _root, **_kwargs: False,
        )
        for summary in summaries:
            summary["busy"] = bool(is_busy(summary["id"]))
            summary["workspace_busy"] = bool(workspace_busy(
                summary.get("workspace_root") or settings.workspace_root,
                exclude_session=summary["id"],
            ))
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

    @app.get("/api/permissions")
    async def get_global_permissions(
        _user: str = Depends(local_operator),
    ):
        return permission_payload(app.state.sessions.get_permission_settings())

    @app.put("/api/permissions")
    async def update_global_permissions(
        req: PermissionUpdateRequest,
        user: str = Depends(local_operator),
    ):
        previous = app.state.sessions.get_permission_settings()
        if req.mode == PermissionMode.FULL_ACCESS:
            available, reason = full_access_status()
            if not available:
                raise HTTPException(
                    403, detail={
                        "code": "full_access_disabled", "message": reason,
                    })
            if not previous.full_access_visible:
                raise HTTPException(
                    403, detail={
                        "code": "full_access_hidden",
                        "message": (
                            "完全访问入口尚未显示。请先在“权限与安全”中阅读"
                            "独立警告并显式显示该高风险模式。"
                        ),
                    })
        try:
            roots = list(req.auto_review_roots)
            if req.mode == PermissionMode.AUTO_REVIEW and not roots:
                roots = [settings.workspace_root]
            def mutate_permissions(_connection):
                # 可见性本身不递增执行授权版本，因此必须在同一写事务内再
                # 校验一次，封住“检查后并发隐藏、随后仍开启”的竞态。
                if (req.mode == PermissionMode.FULL_ACCESS
                        and not app.state.sessions.get_permission_settings(
                        ).full_access_visible):
                    raise SessionPermissionError(
                        "full_access_hidden",
                        "完全访问入口已被隐藏，请先重新显式显示。",
                    )
                context = app.state.sessions.set_permission_settings(
                    mode=req.mode,
                    auto_review_roots=roots,
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
                    "source": "global_settings",
                    "from_mode": previous.mode.value,
                    "to_mode": context.mode.value,
                    "auto_review_roots": context.auto_review_roots,
                    "previous_version": previous.version,
                    "version": context.version,
                }

            context = audited_permission_mutation(
                "__permissions__", "permission_changed", mutate_permissions)
        except PermissionVersionConflict as exc:
            raise permission_http_error(exc, 409) from exc
        except SessionPermissionError as exc:
            raise permission_http_error(exc) from exc
        app.state.permission_requests.revoke_all(operator=user)
        return permission_payload(context)

    @app.put("/api/permissions/full-access-visibility")
    async def update_full_access_visibility(
        req: FullAccessVisibilityUpdateRequest,
        user: str = Depends(local_operator),
    ):
        previous = app.state.sessions.get_permission_settings()
        if req.visible:
            available, reason = full_access_status()
            if not available:
                raise HTTPException(
                    403, detail={
                        "code": "full_access_disabled", "message": reason,
                    })
        if previous.full_access_visible == req.visible:
            if previous.version != req.version:
                raise permission_http_error(PermissionVersionConflict(), 409)
            return permission_payload(previous)
        try:
            def mutate_visibility(_connection):
                context = app.state.sessions.set_full_access_visibility(
                    visible=req.visible,
                    expected_version=req.version,
                    updated_by=user,
                    commit=False,
                )
                return context, {
                    "operator": user,
                    "source": "full_access_visibility",
                    "from_visible": previous.full_access_visible,
                    "to_visible": context.full_access_visible,
                    "from_mode": previous.mode.value,
                    "to_mode": context.mode.value,
                    "previous_version": previous.version,
                    "version": context.version,
                }

            context = audited_permission_mutation(
                "__permissions__",
                "full_access_visibility_changed",
                mutate_visibility,
            )
        except PermissionVersionConflict as exc:
            raise permission_http_error(exc, 409) from exc
        if context.version != previous.version:
            app.state.permission_requests.revoke_all(operator=user)
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
        authorized_path = None
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
                        if req.authorized_path:
                            raise SessionPermissionError(
                                "unexpected_permission_options",
                                "拒绝操作时不能附带自动执行范围。",
                            )
                    elif req.decision in {
                        PermissionDecision.ALLOW_ONCE,
                        PermissionDecision.ALLOW_SESSION,
                    }:
                        if req.authorized_path:
                            raise SessionPermissionError(
                                "unexpected_permission_options",
                                "单次或会话授权不能附带自动执行范围。",
                            )
                        grant_expiry = expires_after(
                            settings.permission_default_ttl)
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
                    elif req.decision == PermissionDecision.AUTHORIZE_PATH:
                        if context.mode != PermissionMode.AUTO_REVIEW:
                            raise SessionPermissionError(
                                "auto_review_required",
                                "只有自动审核模式可以扩展自动执行范围。",
                            )
                        authorized_path = normalize_auto_review_root(
                            req.authorized_path or pending.suggested_path)
                        roots = list(context.auto_review_roots)
                        roots.append(authorized_path)
                        changed_context = app.state.sessions.set_permission_settings(
                            mode=context.mode,
                            auto_review_roots=roots,
                            expected_version=pending.context_version,
                            updated_by=user,
                            commit=False,
                        )
                        app.state.audit.append(
                            "__permissions__",
                            "permission_changed",
                            {
                                "operator": user,
                                "source": "permission_resolution",
                                "from_mode": context.mode.value,
                                "to_mode": changed_context.mode.value,
                                "authorized_path": authorized_path,
                                "auto_review_roots": changed_context.auto_review_roots,
                                "previous_version": context.version,
                                "version": changed_context.version,
                            },
                            connection=connection,
                            commit=False,
                            lock_held=True,
                        )

                    resolution = PermissionResolution(
                        request_id=request_id,
                        decision=req.decision,
                        operator=user,
                        context_version=pending.context_version,
                        grant_id=grant.id if grant else None,
                        authorized_path=authorized_path,
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
                            "authorized_path": authorized_path,
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
        if changed_context is not None:
            app.state.permission_requests.revoke_all(
                operator=user, exclude_request_id=request_id)
        return {
            "ok": True,
            "resolution": resolution.model_dump(mode="json"),
            "permission": (permission_payload(changed_context)
                           if changed_context else None),
        }

    @app.get("/api/audit/scopes")
    async def audit_scopes(_user: str = Depends(local_operator)):
        return {"scopes": [
            {
                "id": scope_id,
                "title": title,
                "event_count": len(app.state.audit.events(scope_id)),
            }
            for scope_id, title in _SYSTEM_AUDIT_SCOPES.items()
        ]}

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str,
                             _user: str = Depends(local_operator)):
        if (session_id not in _SYSTEM_AUDIT_SCOPES
                and not app.state.sessions.exists(session_id)):
            raise HTTPException(404, "会话不存在")
        return {"events": app.state.audit.events(session_id)}

    @app.get("/api/sessions/{session_id}/verify")
    async def session_verify(session_id: str,
                             _user: str = Depends(local_operator)):
        if (session_id not in _SYSTEM_AUDIT_SCOPES
                and not app.state.sessions.exists(session_id)):
            raise HTTPException(404, "会话不存在")
        return {"ok": app.state.audit.verify_chain(session_id)}

    @app.get("/api/stats")
    async def stats(_user: str = Depends(local_operator)):
        return {"sessions": len(app.state.sessions.list(include_drafts=False)),
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
