"""可热更新的模型提供商配置、会话路由与只写凭据存储。

模型元数据保存在主 SQLite 数据库；API Key 则保存在工作区外、仅控制面
账户可读的独立文件中。提供商、模型和默认值均以图形界面的持久化配置为准。
"""
from __future__ import annotations

import contextvars
import hashlib
import ipaddress
import json
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from kylinguard.config import Settings
from kylinguard.llm import LLMClient

ADAPTERS = {"openai", "deepseek", "dashscope", "openai_compatible"}
EFFORTS = {"auto", "none", "minimal", "low", "medium", "high", "xhigh", "max"}
_REF_RE = re.compile(r"^[a-f0-9]{32}$")
_MAX_SECRET_BYTES = 16 * 1024
_MAX_MODELS = 256

# `/models` 没有标准化能力字段。OpenAI Compatible 生态普遍以 low / medium /
# high 作为最小公分母（Chatbox 也采用这组三档）；更特殊的 minimal / xhigh /
# max 不自动猜测。DeepSeek 使用其官方明确的开关与 high / max 语义。
_DISCOVERED_EFFORTS_BY_ADAPTER = {
    "openai": ["low", "medium", "high"],
    "openai_compatible": ["low", "medium", "high"],
    "deepseek": ["none", "high", "max"],
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_providers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    adapter TEXT NOT NULL,
    base_url TEXT NOT NULL,
    secret_ref TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    allow_insecure_http INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    last_tested_at REAL,
    last_test_ok INTEGER
);
CREATE TABLE IF NOT EXISTS llm_models (
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    label TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    supported_efforts TEXT NOT NULL DEFAULT '[]',
    supports_temperature INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(provider_id, model_id),
    FOREIGN KEY(provider_id) REFERENCES llm_providers(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS llm_defaults (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    agent_provider_id TEXT NOT NULL,
    agent_model_id TEXT NOT NULL,
    agent_reasoning_effort TEXT NOT NULL DEFAULT 'auto',
    reviewer_provider_id TEXT NOT NULL,
    reviewer_model_id TEXT NOT NULL,
    reviewer_reasoning_effort TEXT NOT NULL DEFAULT 'auto',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS session_llm_settings (
    session_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    reasoning_effort TEXT NOT NULL DEFAULT 'auto',
    version INTEGER NOT NULL DEFAULT 1,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_session_llm_provider
    ON session_llm_settings(provider_id, model_id);
"""


class LLMConfigError(ValueError):
    """可安全公开的配置错误。"""

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class LLMConfigVersionConflict(LLMConfigError):
    def __init__(self):
        super().__init__(
            "llm_config_version_conflict",
            "模型配置已被其他操作修改，请刷新后重试。",
            status_code=409,
        )


@dataclass(frozen=True)
class ModelSelection:
    provider_id: str
    model_id: str
    reasoning_effort: str = "auto"

    def public_payload(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass(frozen=True)
class ResolvedModel:
    provider_id: str
    provider_name: str
    provider_revision: int
    model_id: str
    reasoning_effort: str
    adapter: str
    supports_temperature: bool
    client: LLMClient

    def public_payload(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "provider_name": self.provider_name,
            "provider_revision": self.provider_revision,
            "model_id": self.model_id,
            "reasoning_effort": self.reasoning_effort,
        }


@dataclass(frozen=True)
class ResolvedModelBundle:
    agent: ResolvedModel
    reviewer: ResolvedModel
    session_version: int
    defaults_version: int

    def public_payload(self) -> dict:
        return {
            "agent": self.agent.public_payload(),
            "reviewer": self.reviewer.public_payload(),
            "session_version": self.session_version,
            "defaults_version": self.defaults_version,
        }


class ProviderSecretStore:
    """使用随机文件引用保存 Key；不实现自制加密。"""

    def __init__(self, directory: str | Path | None = None):
        if directory:
            root = Path(directory).expanduser()
        else:
            state_home = os.environ.get("XDG_STATE_HOME", "").strip()
            root = (Path(state_home).expanduser() if state_home
                    else Path.home() / ".local" / "state")
            root = root / "kylinguard" / "provider-secrets"
        self.directory = root.absolute()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise LLMConfigError(
                "credential_directory_invalid", "模型凭据目录不能是符号链接。")
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        info = self.directory.stat()
        if (hasattr(os, "geteuid") and info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise LLMConfigError(
                "credential_directory_permissions",
                "模型凭据目录必须归当前控制面账户所有且权限为 0700。",
            )
        self._dir_fd: int | None = None
        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            self._dir_fd = os.open(self.directory, flags)
        except (OSError, TypeError):
            # Windows 缺少完整 dir_fd/O_NOFOLLOW 语义；目标部署是 WSL/Linux，
            # Windows 仅保留 lstat + 随机文件名的兼容路径。
            if os.name == "posix":
                raise LLMConfigError(
                    "credential_directory_invalid", "无法安全打开模型凭据目录。")

    def _path(self, secret_ref: str) -> Path:
        if not _REF_RE.fullmatch(secret_ref):
            raise LLMConfigError("credential_reference_invalid", "模型凭据引用无效。")
        return self.directory / secret_ref

    def write(self, secret: str) -> str:
        data = secret.strip().encode("utf-8")
        if not data:
            raise LLMConfigError("api_key_required", "API Key 不能为空。")
        if b"\x00" in data or len(data) > _MAX_SECRET_BYTES:
            raise LLMConfigError("api_key_invalid", "API Key 格式或长度无效。")
        secret_ref = uuid.uuid4().hex
        path = self._path(secret_ref)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = (os.open(secret_ref, flags, 0o600, dir_fd=self._dir_fd)
              if self._dir_fd is not None else os.open(path, flags, 0o600))
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                view = view[written:]
            os.fsync(fd)
            mode = os.fstat(fd).st_mode
            if not stat.S_ISREG(mode):
                raise LLMConfigError(
                    "credential_storage_invalid", "模型凭据存储不是普通文件。")
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        except Exception:
            self.delete(secret_ref)
            raise
        finally:
            os.close(fd)
        return secret_ref

    def read(self, secret_ref: str) -> str:
        if not secret_ref:
            return ""
        path = self._path(secret_ref)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = (os.open(secret_ref, flags, dir_fd=self._dir_fd)
                  if self._dir_fd is not None else os.open(path, flags))
        except FileNotFoundError as exc:
            raise LLMConfigError(
                "api_key_unavailable", "已保存的 API Key 不可用，请重新输入。") from exc
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_SECRET_BYTES:
                raise LLMConfigError(
                    "credential_storage_invalid", "模型凭据文件无效。")
            chunks = []
            remaining = _MAX_SECRET_BYTES + 1
            while remaining > 0:
                chunk = os.read(fd, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if len(data) > _MAX_SECRET_BYTES:
                raise LLMConfigError("credential_storage_invalid", "模型凭据文件过大。")
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise LLMConfigError("credential_storage_invalid", "模型凭据文件无效。") from exc
        finally:
            os.close(fd)

    def exists(self, secret_ref: str) -> bool:
        if not secret_ref or not _REF_RE.fullmatch(secret_ref):
            return False
        try:
            info = (os.stat(secret_ref, dir_fd=self._dir_fd,
                            follow_symlinks=False)
                    if self._dir_fd is not None
                    else (self.directory / secret_ref).lstat())
            return stat.S_ISREG(info.st_mode) and info.st_size <= _MAX_SECRET_BYTES
        except OSError:
            return False

    def delete(self, secret_ref: str) -> None:
        if not secret_ref or not _REF_RE.fullmatch(secret_ref):
            return
        path = self.directory / secret_ref
        try:
            if self._dir_fd is not None:
                info = os.stat(secret_ref, dir_fd=self._dir_fd,
                               follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    return
                os.unlink(secret_ref, dir_fd=self._dir_fd)
            else:
                if path.is_symlink():
                    return
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def cleanup(self, active_refs: set[str], *, minimum_age: float = 3600) -> None:
        try:
            names = (os.listdir(self._dir_fd) if self._dir_fd is not None
                     else [child.name for child in self.directory.iterdir()])
        except OSError:
            return
        for name in names:
            if _REF_RE.fullmatch(name) and name not in active_refs:
                try:
                    info = (os.stat(name, dir_fd=self._dir_fd,
                                    follow_symlinks=False)
                            if self._dir_fd is not None
                            else (self.directory / name).lstat())
                except OSError:
                    continue
                # 另一实例可能处于“先落盘新 Key、后提交引用”的短窗口；
                # 只清理足够旧的孤儿，避免启动竞态删除正在写入的凭据。
                if time.time() - info.st_mtime >= minimum_age:
                    self.delete(name)

    def close(self) -> None:
        if self._dir_fd is not None:
            os.close(self._dir_fd)
            self._dir_fd = None


def normalize_base_url(value: str, *, allow_insecure_http: bool = False) -> str:
    raw = value.strip()
    if not raw or any(ord(char) < 32 for char in raw):
        raise LLMConfigError("base_url_invalid", "模型服务地址无效。")
    try:
        parsed = urlsplit(raw)
        _ = parsed.port
    except ValueError as exc:
        raise LLMConfigError("base_url_invalid", "模型服务地址无效。") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise LLMConfigError("base_url_invalid", "模型服务地址必须是 HTTP(S) URL。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise LLMConfigError(
            "base_url_credentials_forbidden",
            "模型服务地址不能包含账号、密码、查询参数或片段。",
        )
    if parsed.scheme == "http" and not allow_insecure_http:
        host = parsed.hostname.lower()
        loopback = host == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if not loopback:
            raise LLMConfigError(
                "insecure_base_url",
                "非本机 HTTP 会明文传输 API Key；请改用 HTTPS，或明确允许可信内网 HTTP。",
            )
    return raw.rstrip("/")


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), port


def _clean_models(models: list[dict]) -> list[dict]:
    if len(models) > _MAX_MODELS:
        raise LLMConfigError("too_many_models", "单个提供商最多配置 256 个模型。")
    cleaned = []
    seen = set()
    for raw in models:
        model_id = str(raw.get("id") or raw.get("model_id") or "").strip()
        label = str(raw.get("label") or model_id).strip()
        if (not model_id or len(model_id) > 256
                or any(ord(char) < 32 for char in model_id)):
            raise LLMConfigError("model_id_invalid", "模型 ID 格式无效。")
        if model_id in seen:
            raise LLMConfigError("duplicate_model", "同一提供商不能配置重复模型。")
        seen.add(model_id)
        efforts = list(dict.fromkeys(raw.get("supported_efforts") or []))
        if "auto" in efforts or any(value not in EFFORTS for value in efforts):
            raise LLMConfigError("reasoning_effort_invalid", "模型推理强度能力无效。")
        cleaned.append({
            "id": model_id,
            "label": label[:256] or model_id,
            "enabled": bool(raw.get("enabled", True)),
            "supported_efforts": efforts,
            "supports_temperature": bool(raw.get("supports_temperature", False)),
        })
    return cleaned


class LLMConfigStore:
    """模型元数据、默认值与会话选择的 SQLite 数据层。"""

    def __init__(self, db_path: str, settings: Settings,
                 secrets_dir: str | Path | None = None):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        # 0.1 版曾把 .env 映射成虚拟 legacy-env 提供商，并允许默认值或
        # 会话固定到该 ID。模型现已完全改由 GUI 管理；启动时清除这些悬空
        # 绑定，让旧会话在配置好新默认值后重新绑定，不再暗中读取环境变量。
        self._conn.execute(
            "DELETE FROM session_llm_settings WHERE provider_id='legacy-env'"
        )
        self._conn.execute(
            "DELETE FROM llm_defaults WHERE agent_provider_id='legacy-env' "
            "OR reviewer_provider_id='legacy-env'"
        )
        self._lock = threading.RLock()
        self._repair_missing_defaults_locked()
        self._conn.commit()
        configured_secrets = (
            secrets_dir if secrets_dir is not None else settings.llm_secrets_dir or None
        )
        if configured_secrets is None:
            state_home = os.environ.get("XDG_STATE_HOME", "").strip()
            state_root = (Path(state_home).expanduser() if state_home
                          else Path.home() / ".local" / "state")
            db_namespace = hashlib.sha256(
                str(Path(db_path).expanduser().resolve()).encode("utf-8")
            ).hexdigest()[:16]
            configured_secrets = (
                state_root / "kylinguard" / "provider-secrets" / db_namespace)
        self.secrets = ProviderSecretStore(configured_secrets)
        refs = {
            row[0] for row in self._conn.execute(
                "SELECT secret_ref FROM llm_providers WHERE secret_ref <> ''"
            ).fetchall()
        }
        self.secrets.cleanup(refs)

    @contextmanager
    def transaction(self):
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def _repair_missing_defaults_locked(self) -> None:
        if self._conn.execute(
                "SELECT 1 FROM llm_defaults WHERE singleton=1").fetchone():
            return
        row = self._conn.execute(
            "SELECT s.provider_id, s.model_id, s.reasoning_effort "
            "FROM session_llm_settings s "
            "JOIN llm_providers p ON p.id=s.provider_id "
            "JOIN llm_models m ON m.provider_id=s.provider_id "
            "AND m.model_id=s.model_id "
            "WHERE p.enabled=1 AND m.enabled=1 "
            "ORDER BY s.updated_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            row = self._conn.execute(
                "SELECT p.id, m.model_id, 'auto' "
                "FROM llm_providers p "
                "JOIN llm_models m ON m.provider_id=p.id "
                "WHERE p.enabled=1 AND m.enabled=1 "
                "ORDER BY p.created_at, m.rowid LIMIT 1"
            ).fetchone()
        if row is None:
            return
        self._ensure_default_selection_locked(
            ModelSelection(row[0], row[1], row[2]), "system-repair",
        )

    def _models_locked(self, provider_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT model_id, label, enabled, supported_efforts, "
            "supports_temperature FROM llm_models WHERE provider_id=? "
            "ORDER BY rowid",
            (provider_id,),
        ).fetchall()
        result = []
        for row in rows:
            try:
                efforts = json.loads(row[3])
            except (TypeError, ValueError):
                efforts = []
            result.append({
                "id": row[0],
                "label": row[1],
                "enabled": bool(row[2]),
                "supported_efforts": efforts if isinstance(efforts, list) else [],
                "supports_temperature": bool(row[4]),
            })
        return result

    def _public_provider_locked(self, row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "adapter": row["adapter"],
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
            "allow_insecure_http": bool(row["allow_insecure_http"]),
            "api_key_configured": self.secrets.exists(row["secret_ref"]),
            "version": row["version"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_tested_at": row["last_tested_at"],
            "last_test_ok": (None if row["last_test_ok"] is None
                             else bool(row["last_test_ok"])),
            "models": self._models_locked(row["id"]),
        }

    def list_providers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM llm_providers ORDER BY created_at, id"
            ).fetchall()
            return [self._public_provider_locked(row) for row in rows]

    def get_provider(self, provider_id: str, *, public: bool = True):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
            ).fetchone()
            if row is None:
                raise LLMConfigError(
                    "provider_not_found", "模型提供商不存在。", status_code=404)
            return self._public_provider_locked(row) if public else row

    def _insert_models_locked(self, provider_id: str, models: list[dict]) -> None:
        self._conn.executemany(
            "INSERT INTO llm_models(provider_id, model_id, label, enabled, "
            "supported_efforts, supports_temperature) VALUES (?,?,?,?,?,?)",
            [(
                provider_id,
                model["id"],
                model["label"],
                int(model["enabled"]),
                json.dumps(model["supported_efforts"], ensure_ascii=False),
                int(model["supports_temperature"]),
            ) for model in models],
        )

    def _ensure_default_selection_locked(
        self, selection: ModelSelection, user: str,
    ) -> None:
        if self._conn.execute(
                "SELECT 1 FROM llm_defaults WHERE singleton=1").fetchone():
            return
        now = time.time()
        self._conn.execute(
            "INSERT INTO llm_defaults(singleton, agent_provider_id, "
            "agent_model_id, agent_reasoning_effort, reviewer_provider_id, "
            "reviewer_model_id, reviewer_reasoning_effort, version, "
            "updated_at, updated_by) VALUES (1,?,?,?,?,?,?,1,?,?)",
            (selection.provider_id, selection.model_id,
             selection.reasoning_effort, selection.provider_id,
             selection.model_id, selection.reasoning_effort, now, user),
        )

    def _ensure_defaults_locked(self, provider_id: str,
                                models: list[dict], user: str) -> None:
        first = next((model for model in models if model["enabled"]), None)
        if first is None:
            return
        self._ensure_default_selection_locked(
            ModelSelection(provider_id, first["id"], "auto"), user,
        )

    def create_provider(self, *, name: str, adapter: str, base_url: str,
                        api_key: str = "", models: list[dict] | None = None,
                        enabled: bool = True,
                        allow_insecure_http: bool = False,
                        updated_by: str = "", audit=None) -> dict:
        name = name.strip()
        if not name or len(name) > 80:
            raise LLMConfigError("provider_name_invalid", "提供商名称无效。")
        if adapter not in ADAPTERS:
            raise LLMConfigError("provider_adapter_invalid", "未知的模型协议适配器。")
        base_url = normalize_base_url(
            base_url, allow_insecure_http=allow_insecure_http)
        cleaned = _clean_models(models or [])
        secret_ref = self.secrets.write(api_key) if api_key.strip() else ""
        provider_id = uuid.uuid4().hex
        now = time.time()
        provider = None
        try:
            with self.transaction():
                self._conn.execute(
                    "INSERT INTO llm_providers(id, name, adapter, base_url, "
                    "secret_ref, enabled, allow_insecure_http, version, "
                    "created_at, updated_at, updated_by) "
                    "VALUES (?,?,?,?,?,?,?,1,?,?,?)",
                    (provider_id, name, adapter, base_url, secret_ref,
                     int(enabled), int(allow_insecure_http), now, now, updated_by),
                )
                self._insert_models_locked(provider_id, cleaned)
                if enabled:
                    self._ensure_defaults_locked(provider_id, cleaned, updated_by)
                row = self._conn.execute(
                    "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
                ).fetchone()
                assert row is not None
                provider = self._public_provider_locked(row)
                if audit is not None:
                    audit(provider, self._conn)
        except Exception:
            self.secrets.delete(secret_ref)
            raise
        assert provider is not None
        return provider

    def _provider_references_locked(self, provider_id: str) -> list[tuple[str, str]]:
        refs: list[tuple[str, str]] = []
        defaults = self._conn.execute(
            "SELECT agent_provider_id, agent_model_id, reviewer_provider_id, "
            "reviewer_model_id FROM llm_defaults WHERE singleton=1"
        ).fetchone()
        if defaults:
            if defaults[0] == provider_id:
                refs.append(("默认 Agent", defaults[1]))
            if defaults[2] == provider_id:
                refs.append(("安全 Reviewer", defaults[3]))
        rows = self._conn.execute(
            "SELECT session_id, model_id FROM session_llm_settings "
            "WHERE provider_id=? LIMIT 20", (provider_id,)
        ).fetchall()
        refs.extend((f"会话 {row[0][:8]}", row[1]) for row in rows)
        return refs

    def update_provider(self, provider_id: str, *, expected_version: int,
                        name: str, adapter: str, base_url: str,
                        models: list[dict], enabled: bool,
                        allow_insecure_http: bool = False,
                        api_key: str | None = None,
                        clear_api_key: bool = False,
                        updated_by: str = "", audit=None) -> dict:
        if adapter not in ADAPTERS:
            raise LLMConfigError("provider_adapter_invalid", "未知的模型协议适配器。")
        name = name.strip()
        if not name or len(name) > 80:
            raise LLMConfigError("provider_name_invalid", "提供商名称无效。")
        base_url = normalize_base_url(
            base_url, allow_insecure_http=allow_insecure_http)
        cleaned = _clean_models(models)
        new_secret_ref = ""
        old_secret_ref = ""
        provider = None
        try:
            with self.transaction():
                row = self._conn.execute(
                    "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
                ).fetchone()
                if row is None:
                    raise LLMConfigError(
                        "provider_not_found", "模型提供商不存在。", status_code=404)
                if row["version"] != expected_version:
                    raise LLMConfigVersionConflict()
                old_secret_ref = row["secret_ref"]
                replacing_key = api_key is not None and bool(api_key.strip())
                if (_origin(row["base_url"]) != _origin(base_url)
                        and old_secret_ref and not replacing_key and not clear_api_key):
                    raise LLMConfigError(
                        "api_key_required_for_origin_change",
                        "模型服务主机已变化，必须重新输入 API Key，原 Key 不会自动转发。",
                    )
                if replacing_key:
                    new_secret_ref = self.secrets.write(api_key or "")
                    target_secret_ref = new_secret_ref
                elif clear_api_key:
                    target_secret_ref = ""
                else:
                    target_secret_ref = old_secret_ref

                refs = self._provider_references_locked(provider_id)
                new_models = {model["id"]: model for model in cleaned}
                if refs and not enabled:
                    raise LLMConfigError(
                        "provider_in_use", "该提供商仍被默认配置或会话使用，不能禁用。",
                        status_code=409,
                    )
                for label, model_id in refs:
                    model = new_models.get(model_id)
                    if model is None or not model["enabled"]:
                        raise LLMConfigError(
                            "model_in_use",
                            f"{label} 正在使用模型 {model_id}，不能删除或禁用它。",
                            status_code=409,
                        )

                now = time.time()
                self._conn.execute(
                    "UPDATE llm_providers SET name=?, adapter=?, base_url=?, "
                    "secret_ref=?, enabled=?, allow_insecure_http=?, "
                    "version=version+1, updated_at=?, updated_by=? WHERE id=?",
                    (name, adapter, base_url, target_secret_ref, int(enabled),
                     int(allow_insecure_http), now, updated_by, provider_id),
                )
                self._conn.execute(
                    "DELETE FROM llm_models WHERE provider_id=?", (provider_id,))
                self._insert_models_locked(provider_id, cleaned)
                if enabled:
                    self._ensure_defaults_locked(provider_id, cleaned, updated_by)
                row = self._conn.execute(
                    "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
                ).fetchone()
                assert row is not None
                provider = self._public_provider_locked(row)
                if audit is not None:
                    audit(provider, self._conn)
        except Exception:
            self.secrets.delete(new_secret_ref)
            raise
        if new_secret_ref or clear_api_key:
            self.secrets.delete(old_secret_ref)
        assert provider is not None
        return provider

    def delete_provider(self, provider_id: str, *,
                        expected_version: int | None = None, audit=None) -> None:
        secret_ref = ""
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
            ).fetchone()
            if row is None:
                raise LLMConfigError(
                    "provider_not_found", "模型提供商不存在。", status_code=404)
            if expected_version is not None and row["version"] != expected_version:
                raise LLMConfigVersionConflict()
            if self._provider_references_locked(provider_id):
                raise LLMConfigError(
                    "provider_in_use", "该提供商仍被默认配置或会话使用，不能删除。",
                    status_code=409,
                )
            secret_ref = row["secret_ref"]
            provider = self._public_provider_locked(row)
            self._conn.execute("DELETE FROM llm_providers WHERE id=?", (provider_id,))
            if audit is not None:
                audit(provider, self._conn)
        self.secrets.delete(secret_ref)

    def _defaults_locked(self) -> dict:
        row = self._conn.execute(
            "SELECT * FROM llm_defaults WHERE singleton=1").fetchone()
        if row is None:
            return {
                "version": 0,
                "agent": ModelSelection("", "", "auto").public_payload(),
                "reviewer": ModelSelection("", "", "auto").public_payload(),
            }
        return {
            "version": row["version"],
            "agent": ModelSelection(
                row["agent_provider_id"], row["agent_model_id"],
                row["agent_reasoning_effort"],
            ).public_payload(),
            "reviewer": ModelSelection(
                row["reviewer_provider_id"], row["reviewer_model_id"],
                row["reviewer_reasoning_effort"],
            ).public_payload(),
        }

    def get_defaults(self) -> dict:
        with self._lock:
            return self._defaults_locked()

    def _model_locked(self, provider_id: str, model_id: str) -> tuple[dict, dict]:
        row = self._conn.execute(
            "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
        ).fetchone()
        if row is None:
            provider = None
            model = None
        else:
            provider = self._public_provider_locked(row)
            model = next(
                (item for item in provider["models"] if item["id"] == model_id),
                None,
            )
        if provider is None:
            raise LLMConfigError(
                "provider_not_found", "所选模型提供商不存在。", status_code=404)
        if not provider["enabled"]:
            raise LLMConfigError("provider_disabled", "所选模型提供商已禁用。")
        if model is None:
            raise LLMConfigError(
                "model_not_configured", "所选模型不在可用模型列表中。", status_code=404)
        if not model["enabled"]:
            raise LLMConfigError("model_disabled", "所选模型已禁用。")
        return provider, model

    def _validate_selection_locked(self, selection: ModelSelection) -> tuple[dict, dict]:
        if selection.reasoning_effort not in EFFORTS:
            raise LLMConfigError("reasoning_effort_invalid", "未知的推理强度。")
        provider, model = self._model_locked(
            selection.provider_id, selection.model_id)
        if (selection.reasoning_effort != "auto"
                and selection.reasoning_effort not in model["supported_efforts"]):
            raise LLMConfigError(
                "reasoning_effort_unsupported",
                "所选模型不支持该推理强度，请改用自动或模型声明的档位。",
            )
        return provider, model

    def validate_selection(self, selection: ModelSelection) -> None:
        with self._lock:
            self._validate_selection_locked(selection)

    def update_defaults(self, *, agent: ModelSelection,
                        reviewer: ModelSelection, expected_version: int,
                        updated_by: str = "", audit=None) -> dict:
        result = None
        with self.transaction():
            row = self._conn.execute(
                "SELECT version FROM llm_defaults WHERE singleton=1").fetchone()
            current_version = row[0] if row else 0
            if current_version != expected_version:
                raise LLMConfigVersionConflict()
            self._validate_selection_locked(agent)
            self._validate_selection_locked(reviewer)
            now = time.time()
            if row is None:
                next_version = 1
                self._conn.execute(
                    "INSERT INTO llm_defaults(singleton, agent_provider_id, "
                    "agent_model_id, agent_reasoning_effort, reviewer_provider_id, "
                    "reviewer_model_id, reviewer_reasoning_effort, version, "
                    "updated_at, updated_by) VALUES (1,?,?,?,?,?,?,?,?,?)",
                    (agent.provider_id, agent.model_id, agent.reasoning_effort,
                     reviewer.provider_id, reviewer.model_id,
                     reviewer.reasoning_effort, next_version, now, updated_by),
                )
            else:
                next_version = current_version + 1
                self._conn.execute(
                    "UPDATE llm_defaults SET agent_provider_id=?, "
                    "agent_model_id=?, agent_reasoning_effort=?, "
                    "reviewer_provider_id=?, reviewer_model_id=?, "
                    "reviewer_reasoning_effort=?, version=?, updated_at=?, "
                    "updated_by=? WHERE singleton=1",
                    (agent.provider_id, agent.model_id, agent.reasoning_effort,
                     reviewer.provider_id, reviewer.model_id,
                     reviewer.reasoning_effort, next_version, now, updated_by),
                )
            result = {
                "version": next_version,
                "agent": agent.public_payload(),
                "reviewer": reviewer.public_payload(),
            }
            if audit is not None:
                audit(result, self._conn)
        assert result is not None
        return result

    def _session_payload(self, row: sqlite3.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "provider_id": row["provider_id"],
            "model_id": row["model_id"],
            "reasoning_effort": row["reasoning_effort"],
            "version": row["version"],
            "updated_at": row["updated_at"],
        }

    def ensure_session(self, session_id: str, *,
                       selection: ModelSelection | None = None,
                       updated_by: str = "") -> dict:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM session_llm_settings WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is not None:
                if selection is not None:
                    existing = ModelSelection(
                        row["provider_id"], row["model_id"],
                        row["reasoning_effort"],
                    )
                    if existing != selection:
                        raise LLMConfigError(
                            "session_model_already_pinned",
                            "会话已固定模型，请通过会话模型接口切换。",
                            status_code=409,
                        )
                return self._session_payload(row)
            if selection is None:
                default = self._defaults_locked()["agent"]
                if not default["provider_id"] or not default["model_id"]:
                    raise LLMConfigError(
                        "model_configuration_required",
                        "尚未配置默认模型，请先在“模型服务”中添加提供商和模型。",
                        status_code=409,
                    )
                selection = ModelSelection(
                    default["provider_id"], default["model_id"],
                    default["reasoning_effort"],
                )
            self._validate_selection_locked(selection)
            now = time.time()
            self._conn.execute(
                "INSERT INTO session_llm_settings(session_id, provider_id, "
                "model_id, reasoning_effort, version, updated_at, updated_by) "
                "VALUES (?,?,?,?,1,?,?)",
                (session_id, selection.provider_id, selection.model_id,
                 selection.reasoning_effort, now, updated_by),
            )
            row = self._conn.execute(
                "SELECT * FROM session_llm_settings WHERE session_id=?",
                (session_id,),
            ).fetchone()
            assert row is not None
            return self._session_payload(row)

    def create_session_with_connection(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        selection: ModelSelection,
        *,
        updated_by: str = "",
    ) -> dict:
        """在调用方的同库事务中创建模型绑定，用于原子会话创建。"""
        if selection.reasoning_effort not in EFFORTS:
            raise LLMConfigError("reasoning_effort_invalid", "未知的推理强度。")
        row = connection.execute(
            "SELECT p.enabled, m.enabled, m.supported_efforts "
            "FROM llm_providers p JOIN llm_models m ON m.provider_id=p.id "
            "WHERE p.id=? AND m.model_id=?",
            (selection.provider_id, selection.model_id),
        ).fetchone()
        if row is None:
            raise LLMConfigError(
                "model_not_configured", "所选提供商或模型不存在。",
                status_code=404,
            )
        provider_enabled = bool(row[0])
        try:
            efforts = json.loads(row[2])
        except (TypeError, ValueError):
            efforts = []
        model = {
            "enabled": bool(row[1]),
            "supported_efforts": efforts if isinstance(efforts, list) else [],
        }
        if not provider_enabled:
            raise LLMConfigError("provider_disabled", "所选模型提供商已禁用。")
        if model is None:
            raise LLMConfigError(
                "model_not_configured", "所选模型不在可用模型列表中。",
                status_code=404,
            )
        if not model["enabled"]:
            raise LLMConfigError("model_disabled", "所选模型已禁用。")
        if (selection.reasoning_effort != "auto"
                and selection.reasoning_effort not in model["supported_efforts"]):
            raise LLMConfigError(
                "reasoning_effort_unsupported", "所选模型不支持该推理强度。")

        existing = connection.execute(
            "SELECT provider_id, model_id, reasoning_effort, version, updated_at "
            "FROM session_llm_settings WHERE session_id=?", (session_id,),
        ).fetchone()
        if existing is not None:
            current = ModelSelection(existing[0], existing[1], existing[2])
            if current != selection:
                raise LLMConfigError(
                    "session_model_already_pinned",
                    "会话已固定模型，请通过会话模型接口切换。",
                    status_code=409,
                )
            return {
                "session_id": session_id,
                **current.public_payload(),
                "version": existing[3],
                "updated_at": existing[4],
            }
        now = time.time()
        connection.execute(
            "INSERT INTO session_llm_settings(session_id, provider_id, "
            "model_id, reasoning_effort, version, updated_at, updated_by) "
            "VALUES (?,?,?,?,1,?,?)",
            (session_id, selection.provider_id, selection.model_id,
             selection.reasoning_effort, now, updated_by),
        )
        if not connection.execute(
                "SELECT 1 FROM llm_defaults WHERE singleton=1").fetchone():
            connection.execute(
                "INSERT INTO llm_defaults(singleton, agent_provider_id, "
                "agent_model_id, agent_reasoning_effort, reviewer_provider_id, "
                "reviewer_model_id, reviewer_reasoning_effort, version, "
                "updated_at, updated_by) VALUES (1,?,?,?,?,?,?,1,?,?)",
                (selection.provider_id, selection.model_id,
                 selection.reasoning_effort, selection.provider_id,
                 selection.model_id, selection.reasoning_effort, now, updated_by),
            )
        return {
            "session_id": session_id,
            **selection.public_payload(),
            "version": 1,
            "updated_at": now,
        }

    def get_session(self, session_id: str, *, ensure: bool = True) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM session_llm_settings WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is not None:
                return self._session_payload(row)
        return self.ensure_session(session_id) if ensure else None

    def update_session(self, session_id: str, *, selection: ModelSelection,
                       expected_version: int, updated_by: str = "",
                       audit=None) -> dict:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM session_llm_settings WHERE session_id=?",
                (session_id,),
            ).fetchone()
            if row is None:
                raise LLMConfigError(
                    "session_model_not_found", "会话模型配置不存在。", status_code=404)
            if row["version"] != expected_version:
                raise LLMConfigVersionConflict()
            self._validate_selection_locked(selection)
            now = time.time()
            self._conn.execute(
                "UPDATE session_llm_settings SET provider_id=?, model_id=?, "
                "reasoning_effort=?, version=version+1, updated_at=?, "
                "updated_by=? WHERE session_id=?",
                (selection.provider_id, selection.model_id,
                 selection.reasoning_effort, now, updated_by, session_id),
            )
            row = self._conn.execute(
                "SELECT * FROM session_llm_settings WHERE session_id=?",
                (session_id,),
            ).fetchone()
            assert row is not None
            result = self._session_payload(row)
            if audit is not None:
                audit(result, self._conn)
            return result

    def public_config(self) -> dict:
        return {"providers": self.list_providers(), "defaults": self.get_defaults()}

    def provider_connection(self, provider_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
            ).fetchone()
            if row is None:
                raise LLMConfigError(
                    "provider_not_found", "模型提供商不存在。", status_code=404)
            provider = self._public_provider_locked(row)
            return {**provider, "api_key": self.secrets.read(row["secret_ref"])}

    def add_discovered_models(self, provider_id: str, model_ids: list[str],
                              *, expected_version: int | None = None,
                              updated_by: str = "", audit=None) -> dict:
        cleaned_ids = []
        for value in model_ids[:_MAX_MODELS]:
            model_id = str(value).strip()
            if (model_id and len(model_id) <= 256
                    and not any(ord(char) < 32 for char in model_id)):
                cleaned_ids.append(model_id)
        cleaned_ids = list(dict.fromkeys(cleaned_ids))
        provider = None
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
            ).fetchone()
            if row is None:
                raise LLMConfigError(
                    "provider_not_found", "模型提供商不存在。", status_code=404)
            if (expected_version is not None
                    and row["version"] != expected_version):
                raise LLMConfigVersionConflict()
            existing_models = self._models_locked(provider_id)
            existing = {model["id"] for model in existing_models}
            discovered_efforts = _DISCOVERED_EFFORTS_BY_ADAPTER.get(
                row["adapter"], [])
            capability_updates = 0
            if discovered_efforts:
                # 对此前已读取但能力为空的 DeepSeek 模型补齐新默认值；管理员
                # 手工声明过的列表不覆盖，避免重新读取时丢失显式配置。
                for model in existing_models:
                    if (model["id"] in cleaned_ids
                            and not model["supported_efforts"]):
                        self._conn.execute(
                            "UPDATE llm_models SET supported_efforts=? "
                            "WHERE provider_id=? AND model_id=?",
                            (json.dumps(discovered_efforts),
                             provider_id, model["id"]),
                        )
                        capability_updates += 1
            additions = [{
                "id": model_id,
                "label": model_id,
                "enabled": True,
                # 除协议能统一保证的能力外，不根据模型名猜测档位。
                "supported_efforts": list(discovered_efforts),
                "supports_temperature": False,
            } for model_id in cleaned_ids if model_id not in existing]
            self._insert_models_locked(provider_id, additions)
            if additions:
                self._ensure_defaults_locked(provider_id, additions, updated_by)
            if additions or capability_updates:
                now = time.time()
                self._conn.execute(
                    "UPDATE llm_providers SET version=version+1, updated_at=?, "
                    "updated_by=? WHERE id=?", (now, updated_by, provider_id))
            row = self._conn.execute(
                "SELECT * FROM llm_providers WHERE id=?", (provider_id,)
            ).fetchone()
            assert row is not None
            provider = self._public_provider_locked(row)
            if audit is not None:
                audit(provider, self._conn)
        assert provider is not None
        return provider

    def mark_test(self, provider_id: str, ok: bool) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE llm_providers SET last_tested_at=?, last_test_ok=? "
                "WHERE id=?", (time.time(), int(ok), provider_id))
            self._conn.commit()

    def runtime_spec(self, session_id: str) -> dict:
        """在同一配置锁内解析一轮所需的全部不可变输入。"""
        with self._lock:
            session = self.get_session(session_id, ensure=True)
            assert session is not None
            defaults = self._defaults_locked()
            agent_selection = ModelSelection(
                session["provider_id"], session["model_id"],
                session["reasoning_effort"],
            )
            reviewer_raw = defaults["reviewer"]
            if reviewer_raw["provider_id"] and reviewer_raw["model_id"]:
                reviewer_selection = ModelSelection(
                    reviewer_raw["provider_id"], reviewer_raw["model_id"],
                    reviewer_raw["reasoning_effort"],
                )
            else:
                reviewer_selection = agent_selection

            def endpoint(selection: ModelSelection) -> dict:
                provider, model = self._validate_selection_locked(selection)
                row = self._conn.execute(
                    "SELECT secret_ref FROM llm_providers WHERE id=?",
                    (selection.provider_id,),
                ).fetchone()
                assert row is not None
                try:
                    api_key = self.secrets.read(row[0])
                except LLMConfigError as exc:
                    if exc.code != "api_key_unavailable":
                        raise
                    api_key = ""
                return {
                    "provider_id": selection.provider_id,
                    "provider_name": provider["name"],
                    "provider_revision": provider["version"],
                    "base_url": provider["base_url"],
                    "api_key": api_key,
                    "adapter": provider["adapter"],
                    "model_id": selection.model_id,
                    "reasoning_effort": selection.reasoning_effort,
                    "supports_temperature": model["supports_temperature"],
                }

            return {
                "agent": endpoint(agent_selection),
                "reviewer": endpoint(reviewer_selection),
                "session_version": session["version"],
                "defaults_version": defaults["version"],
            }

    def close(self) -> None:
        self.secrets.close()
        self._conn.close()


class LLMRuntime:
    """按会话解析模型，并以 ContextVar 将一轮绑定到路由客户端。"""

    def __init__(self, store: LLMConfigStore, settings: Settings):
        self.store = store
        self.settings = settings
        self._current: contextvars.ContextVar[ResolvedModelBundle | None] = (
            contextvars.ContextVar("kylinguard_llm_bundle", default=None)
        )

    def _client(self, spec: dict) -> LLMClient:
        return LLMClient(
            spec["base_url"], spec["api_key"], spec["model_id"],
            self.settings.llm_max_retries, self.settings.llm_timeout,
            adapter=spec["adapter"],
            reasoning_effort=spec["reasoning_effort"],
            supports_temperature=spec["supports_temperature"],
        )

    def resolve(self, session_id: str) -> ResolvedModelBundle:
        raw = self.store.runtime_spec(session_id)

        def resolved(spec: dict) -> ResolvedModel:
            return ResolvedModel(
                provider_id=spec["provider_id"],
                provider_name=spec["provider_name"],
                provider_revision=spec["provider_revision"],
                model_id=spec["model_id"],
                reasoning_effort=spec["reasoning_effort"],
                adapter=spec["adapter"],
                supports_temperature=spec["supports_temperature"],
                client=self._client(spec),
            )

        return ResolvedModelBundle(
            agent=resolved(raw["agent"]),
            reviewer=resolved(raw["reviewer"]),
            session_version=raw["session_version"],
            defaults_version=raw["defaults_version"],
        )

    @asynccontextmanager
    async def bind(self, session_id: str):
        bundle = self.resolve(session_id)
        token = self._current.set(bundle)
        try:
            yield bundle
        finally:
            self._current.reset(token)
            clients = [bundle.agent.client]
            if bundle.reviewer.client is not bundle.agent.client:
                clients.append(bundle.reviewer.client)
            for client in clients:
                try:
                    await client.close()
                except Exception:
                    # 连接回收失败不能覆盖已经形成审计终态的任务结果。
                    pass

    def current(self) -> ResolvedModelBundle:
        bundle = self._current.get()
        if bundle is None:
            raise RuntimeError("LLM 路由客户端只能在会话轮次中调用")
        return bundle

    def routed_client(self, role: str):
        if role not in {"agent", "reviewer"}:
            raise ValueError("unknown llm role")
        return RoutedLLMClient(self, role)

    async def _remote_model_ids(self, provider_id: str) -> list[str]:
        connection = self.store.provider_connection(provider_id)
        if not connection["api_key"].strip():
            raise LLMConfigError("api_key_required", "尚未配置模型服务 API Key。")
        url = f"{connection['base_url'].rstrip('/')}/models"
        body = bytearray()
        async with httpx.AsyncClient(
            follow_redirects=False,
            timeout=min(30.0, self.settings.llm_timeout),
        ) as client:
            async with client.stream(
                "GET", url,
                headers={"Authorization": f"Bearer {connection['api_key']}"},
            ) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > 2 * 1024 * 1024:
                        raise LLMConfigError(
                            "model_list_too_large",
                            "模型服务返回的模型列表过大，已停止读取。",
                        )
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, ValueError) as exc:
            raise LLMConfigError(
                "model_list_invalid", "模型服务返回了无法解析的模型列表。") from exc
        raw_models = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(raw_models, list):
            raise LLMConfigError(
                "model_list_invalid", "模型服务没有返回兼容的模型列表。")
        ids = []
        for model in raw_models[:_MAX_MODELS]:
            model_id = str(model.get("id", "")).strip() if isinstance(model, dict) else ""
            if (model_id and len(model_id) <= 256
                    and not any(ord(char) < 32 for char in model_id)):
                ids.append(model_id)
        return list(dict.fromkeys(ids))

    async def test_provider(self, provider_id: str) -> dict:
        started = time.monotonic()
        try:
            ids = await self._remote_model_ids(provider_id)
        except Exception:
            self.store.mark_test(provider_id, False)
            raise
        self.store.mark_test(provider_id, True)
        return {
            "ok": True,
            "latency_ms": int((time.monotonic() - started) * 1000),
            "model_count": len(ids),
        }

    async def fetch_model_ids(self, provider_id: str) -> list[str]:
        try:
            ids = await self._remote_model_ids(provider_id)
        except Exception:
            self.store.mark_test(provider_id, False)
            raise
        self.store.mark_test(provider_id, True)
        return ids

    async def discover_models(self, provider_id: str, *, updated_by: str,
                              audit=None) -> dict:
        ids = await self.fetch_model_ids(provider_id)
        provider = self.store.add_discovered_models(
            provider_id, ids, updated_by=updated_by, audit=audit)
        return {"provider": provider, "discovered": len(ids)}


class RoutedLLMClient:
    """保持 Planner/Reviewer 现有接口，同时把调用转发到本轮快照。"""

    def __init__(self, runtime: LLMRuntime, role: str):
        self._runtime = runtime
        self._role = role

    @property
    def model(self) -> str:
        return getattr(self._runtime.current(), self._role).model_id

    def _delegate(self) -> LLMClient:
        return getattr(self._runtime.current(), self._role).client

    async def chat(self, *args, **kwargs):
        return await self._delegate().chat(*args, **kwargs)

    async def chat_stream(self, *args, **kwargs):
        async for delta in self._delegate().chat_stream(*args, **kwargs):
            yield delta
