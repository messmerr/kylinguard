"""自定义 MCP stdio 服务的持久化配置与秘密环境变量存储。

数据库只保存可公开的启动元数据和普通环境变量。``secret_env`` 作为只写
能力保存在工作区外的 0600 随机文件中，API/审计载荷只会看到变量名。
所有运行时配置在交给 MCP 客户端前都会再次校验，避免数据库被离线修改后
绕过控制面的约束。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


BUILTIN_MCP_SERVER_IDS = frozenset({
    "kylin", "sysinfo", "services", "logs", "network", "disk", "security",
    "run_command", "files",
})

_SERVER_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
MCP_TOOL_NAME_PATTERN = r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}"
_TOOL_NAME_RE = re.compile(rf"^{MCP_TOOL_NAME_PATTERN}$")
_SECRET_REF_RE = re.compile(r"^[a-f0-9]{32}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
_SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE_?KEY|"
    r"ACCESS_?KEY|CREDENTIALS?|AUTHORIZATION)(?:$|_)",
    re.IGNORECASE,
)
_OBVIOUS_SECRET_VALUE_RE = re.compile(
    r"(?i)(?:^Bearer\s+|^sk-[A-Za-z0-9]|^ghp_|^github_pat_|^xox[baprs]-|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|^[a-z][a-z0-9+.-]*://[^/@\s]+:"
    r"[^/@\s]+@)"
)
_ASSIGNMENT_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|token|secret|password|passwd|"
    r"authorization)\b(\s*[:=]\s*)([^\s,;]+)"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_SECRET_ARG_RE = re.compile(
    r"(?i)^--?(?:api[-_]?key|access[-_]?token|token|secret|password|passwd|"
    r"authorization)(?:=|$)"
)

_MAX_ARGS = 128
_MAX_ARG_BYTES = 4096
_MAX_ENV_ITEMS = 64
_MAX_ENV_VALUE_BYTES = 16 * 1024
_MAX_ENV_TOTAL_BYTES = 64 * 1024
_MAX_TOOLS = 256
_MAX_TOOL_SCHEMA_BYTES = 128 * 1024
_MAX_TOOLS_JSON_BYTES = 1024 * 1024
_MAX_ERROR_CHARS = 2000
_TOOL_ANNOTATION_KEYS = (
    "title", "readOnlyHint", "destructiveHint", "idempotentHint",
    "openWorldHint",
)


def default_mcp_secrets_directory(db_path: str | Path) -> Path:
    """Return the per-database MCP secret directory outside the workspace.

    Development databases commonly live on a Windows-mounted WSL path where
    POSIX 0700/0600 modes cannot be represented reliably.  Secret files must
    therefore default to the host user's state directory instead of following
    the database directory.  The database digest keeps independent instances
    from cleaning up each other's randomly named secret files.
    """
    state_home = os.environ.get("XDG_STATE_HOME", "").strip()
    state_root = (Path(state_home).expanduser() if state_home
                  else Path.home() / ".local" / "state")
    namespace = hashlib.sha256(
        str(Path(db_path).expanduser().resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return state_root / "kylinguard" / "mcp-secrets" / namespace

# 这些变量可以改变被执行代码、动态链接器或控制面的行为，不能由自定义
# 服务覆盖。PATH/HOME 等基础变量由 safe_subprocess_env 固定提供。
_RESERVED_ENV_KEYS = frozenset({
    "PATH", "HOME", "USERPROFILE", "PYTHONPATH", "PYTHONHOME",
    "PYTHONSTARTUP", "PYTHONINSPECT", "BASH_ENV", "ENV", "SHELLOPTS",
    "NODE_OPTIONS", "RUBYOPT", "PERL5OPT", "GIT_SSH_COMMAND",
})
_RESERVED_ENV_PREFIXES = ("KG_", "LD_", "DYLD_")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS custom_mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    transport TEXT NOT NULL DEFAULT 'stdio' CHECK(transport = 'stdio'),
    command TEXT NOT NULL,
    cwd TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '[]',
    env_json TEXT NOT NULL DEFAULT '{}',
    secret_env_ref TEXT NOT NULL DEFAULT '',
    secret_env_keys_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'untested',
    tools_json TEXT NOT NULL DEFAULT '[]',
    tool_policies_json TEXT NOT NULL DEFAULT '{}',
    tool_count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    updated_by TEXT NOT NULL DEFAULT '',
    last_tested_at REAL,
    last_test_ok INTEGER
);
"""


class MCPConfigError(ValueError):
    """可安全返回给本机控制面的自定义 MCP 配置错误。"""

    def __init__(self, code: str, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class MCPConfigVersionConflict(MCPConfigError):
    def __init__(self):
        super().__init__(
            "mcp_config_version_conflict",
            "MCP 服务配置已被其他操作修改，请刷新后重试。",
            status_code=409,
        )


@dataclass(frozen=True)
class MCPServerConfig:
    """已经校验、可直接交给 stdio 启动器的运行时快照。"""

    id: str
    name: str
    command: str
    cwd: str = ""
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    secret_env: dict[str, str] = field(default_factory=dict, repr=False)
    # 管理员策略保留其绑定的工具定义摘要；运行时必须在 MCP 实际启动并
    # 重新列举工具后再验证摘要，不能只信任上一次测试保存的工具清单。
    tool_policies: dict[str, dict[str, str]] = field(default_factory=dict)
    enabled: bool = False
    version: int = 1

    def process_env(self) -> dict[str, str]:
        """返回显式授予该 MCP 服务的环境，不包含父进程环境。"""
        return {**self.env, **self.secret_env}


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def validate_server_id(server_id: str) -> str:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.fullmatch(server_id):
        raise MCPConfigError(
            "mcp_server_id_invalid",
            "MCP 服务 ID 须以小写字母开头，且只包含小写字母、数字、- 或 _。",
        )
    if server_id in BUILTIN_MCP_SERVER_IDS:
        raise MCPConfigError(
            "mcp_server_id_reserved", "MCP 服务 ID 与内置服务冲突。")
    return server_id


def validate_server_name(name: str) -> str:
    if not isinstance(name, str):
        raise MCPConfigError("mcp_server_name_invalid", "MCP 服务名称无效。")
    cleaned = name.strip()
    if not cleaned or len(cleaned) > 80 or _has_control(cleaned):
        raise MCPConfigError("mcp_server_name_invalid", "MCP 服务名称无效。")
    return cleaned


def validate_command(command: str) -> str:
    if not isinstance(command, str) or not command or len(command) > 4096:
        raise MCPConfigError("mcp_command_invalid", "MCP 启动命令无效。")
    if _has_control(command) or not Path(command).is_absolute():
        raise MCPConfigError(
            "mcp_command_must_be_absolute", "MCP 启动命令必须是绝对路径。")
    if os.path.abspath(command) != command:
        raise MCPConfigError(
            "mcp_command_not_normalized", "MCP 启动命令路径必须规范化。")
    if not Path(command).is_file() or not os.access(command, os.X_OK):
        raise MCPConfigError(
            "mcp_command_not_executable", "MCP 启动命令不存在或不可执行。")
    return command


def validate_cwd(cwd: str | None, *, command: str) -> str:
    """校验自定义 MCP 工作目录，并把缺省值固定为命令目录的真实路径。"""
    if cwd is None or cwd == "":
        try:
            candidate = Path(command).parent.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise MCPConfigError(
                "mcp_cwd_invalid", "无法确定 MCP 启动命令的工作目录。"
            ) from exc
    else:
        if not isinstance(cwd, str) or len(cwd) > 4096 or _has_control(cwd):
            raise MCPConfigError("mcp_cwd_invalid", "MCP 工作目录无效。")
        path = Path(cwd)
        if not path.is_absolute():
            raise MCPConfigError(
                "mcp_cwd_must_be_absolute", "MCP 工作目录必须是绝对路径。"
            )
        try:
            candidate = path.resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as exc:
            raise MCPConfigError(
                "mcp_cwd_not_directory", "MCP 工作目录不存在或不是目录。"
            ) from exc
        # resolve 会消除 ``..``、重复分隔符以及任意符号链接组件。要求调用方
        # 直接提交解析后的路径，避免保存时与启动时落到不同目录。
        if str(candidate) != cwd:
            raise MCPConfigError(
                "mcp_cwd_not_normalized",
                "MCP 工作目录必须规范化，且不能包含符号链接。",
            )
    if not candidate.is_dir() or candidate.is_symlink():
        raise MCPConfigError(
            "mcp_cwd_not_directory", "MCP 工作目录不存在、不是目录或是符号链接。"
        )
    return str(candidate)


def validate_args(args: Sequence[str] | None) -> tuple[str, ...]:
    if args is None:
        return ()
    if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
        raise MCPConfigError(
            "mcp_args_invalid", "MCP 启动参数必须是字符串数组，不能是 shell 文本。")
    if len(args) > _MAX_ARGS:
        raise MCPConfigError("mcp_args_too_many", "MCP 启动参数过多。")
    cleaned: list[str] = []
    for value in args:
        if (not isinstance(value, str)
                or len(value.encode("utf-8")) > _MAX_ARG_BYTES
                or _has_control(value)):
            raise MCPConfigError("mcp_arg_invalid", "MCP 启动参数格式或长度无效。")
        if _SECRET_ARG_RE.search(value) or _OBVIOUS_SECRET_VALUE_RE.search(value):
            raise MCPConfigError(
                "mcp_arg_secret_misclassified",
                "MCP 启动参数疑似包含凭据，请改用 secret_env 只写字段。",
            )
        cleaned.append(value)
    return tuple(cleaned)


def _validate_env_key(key: str) -> str:
    if not isinstance(key, str) or not _ENV_KEY_RE.fullmatch(key):
        raise MCPConfigError(
            "mcp_env_key_invalid", "环境变量名须为大写字母、数字或下划线。")
    if key in _RESERVED_ENV_KEYS or key.startswith(_RESERVED_ENV_PREFIXES):
        raise MCPConfigError(
            "mcp_env_key_reserved", f"环境变量 {key} 会改变执行边界，不能配置。")
    return key


def validate_environment(
    env: Mapping[str, str] | None,
    *,
    secret: bool,
    allow_blank_secret: bool = False,
) -> dict[str, str]:
    if env is None:
        return {}
    if not isinstance(env, Mapping) or len(env) > _MAX_ENV_ITEMS:
        raise MCPConfigError("mcp_env_invalid", "MCP 环境变量配置无效或数量过多。")
    cleaned: dict[str, str] = {}
    total = 0
    for raw_key, value in env.items():
        key = _validate_env_key(raw_key)
        if not isinstance(value, str):
            raise MCPConfigError("mcp_env_value_invalid", "环境变量值必须是字符串。")
        size = len(value.encode("utf-8"))
        total += size
        if (size > _MAX_ENV_VALUE_BYTES or _has_control(value)
                or (secret and not value and not allow_blank_secret)):
            raise MCPConfigError(
                "mcp_env_value_invalid", f"环境变量 {key} 的值格式或长度无效。")
        if not secret and (
            _SECRET_KEY_RE.search(key) or _OBVIOUS_SECRET_VALUE_RE.search(value)
        ):
            raise MCPConfigError(
                "mcp_env_secret_misclassified",
                f"环境变量 {key} 疑似凭据，请通过 secret_env 只写字段保存。",
            )
        cleaned[key] = value
    if total > _MAX_ENV_TOTAL_BYTES:
        raise MCPConfigError("mcp_env_too_large", "MCP 环境变量总大小过大。")
    return cleaned


def make_stdio_server_config(
    *,
    server_id: str,
    name: str,
    command: str,
    cwd: str | None = None,
    args: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    secret_env: Mapping[str, str] | None = None,
    tool_policies: Mapping | None = None,
    enabled: bool = False,
    version: int = 1,
) -> MCPServerConfig:
    """构造用于“保存前测试”等场景的严格运行时配置。"""
    checked_command = validate_command(command)
    regular = validate_environment(env, secret=False)
    secrets = validate_environment(secret_env, secret=True)
    overlap = set(regular) & set(secrets)
    if overlap:
        raise MCPConfigError(
            "mcp_env_key_conflict",
            f"环境变量不能同时是普通值和秘密值：{sorted(overlap)[0]}",
        )
    if not isinstance(version, int) or version < 1:
        raise MCPConfigError("mcp_version_invalid", "MCP 配置版本无效。")
    checked_tool_policies = validate_tool_policies(tool_policies)
    return MCPServerConfig(
        id=validate_server_id(server_id),
        name=validate_server_name(name),
        command=checked_command,
        cwd=validate_cwd(cwd, command=checked_command),
        args=validate_args(args),
        env=regular,
        secret_env=secrets,
        tool_policies=checked_tool_policies,
        enabled=bool(enabled),
        version=version,
    )


def redact_mcp_error(
    error: BaseException | str,
    secret_values: Sequence[str] = (),
    *,
    max_chars: int | None = _MAX_ERROR_CHARS,
) -> str:
    """清理连接错误，避免秘密环境变量出现在 API、数据库或日志。"""
    text = str(error)
    for secret in sorted((value for value in secret_values if value),
                         key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = _BEARER_RE.sub("Bearer [REDACTED]", text)
    text = _ASSIGNMENT_SECRET_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text)
    text = "".join(
        char if char in "\n\t" or ord(char) >= 32 else " " for char in text
    ).strip()
    if max_chars is not None:
        text = text[:max(0, max_chars)]
    return text or type(error).__name__


def _validate_schema_shape(schema: dict, *, depth: int = 0) -> None:
    """校验提示词格式化器会访问的 JSON Schema 结构，防止畸形目录崩溃。"""
    if depth > 24:
        raise MCPConfigError("mcp_tool_schema_too_deep", "MCP 工具参数结构嵌套过深。")
    raw_type = schema.get("type")
    if (raw_type is not None
            and not isinstance(raw_type, str)
            and not (
                isinstance(raw_type, list)
                and raw_type
                and all(isinstance(value, str) for value in raw_type)
            )):
        raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具 type 无效。")
    if "enum" in schema and not isinstance(schema["enum"], list):
        raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具 enum 无效。")
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具 properties 无效。")
    for key, value in properties.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具属性结构无效。")
        _validate_schema_shape(value, depth=depth + 1)
    required = schema.get("required", [])
    if (not isinstance(required, list)
            or any(not isinstance(value, str) for value in required)):
        raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具 required 无效。")
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(keyword, [])
        if (not isinstance(variants, list)
                or any(not isinstance(value, dict) for value in variants)):
            raise MCPConfigError(
                "mcp_tool_schema_invalid", f"MCP 工具 {keyword} 无效。")
        for value in variants:
            _validate_schema_shape(value, depth=depth + 1)
    if "items" in schema:
        items = schema["items"]
        if not isinstance(items, dict):
            raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具 items 无效。")
        _validate_schema_shape(items, depth=depth + 1)


def normalize_tool_annotations(value) -> dict:
    """只保留 MCP 标准 ToolAnnotations；它们仅供管理员参考。"""
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if not isinstance(value, Mapping):
        raise MCPConfigError(
            "mcp_tool_annotations_invalid", "MCP 工具 annotations 无效。")
    result: dict[str, str | bool] = {}
    title = value.get("title")
    if title is not None:
        if not isinstance(title, str):
            raise MCPConfigError(
                "mcp_tool_annotations_invalid", "MCP 工具 annotations.title 无效。")
        cleaned_title = " ".join(title.split())
        if len(cleaned_title) > 200 or _has_control(cleaned_title):
            raise MCPConfigError(
                "mcp_tool_annotations_invalid", "MCP 工具 annotations.title 无效。")
        if cleaned_title:
            result["title"] = cleaned_title
    for key in _TOOL_ANNOTATION_KEYS[1:]:
        raw = value.get(key)
        if raw is None:
            continue
        if not isinstance(raw, bool):
            raise MCPConfigError(
                "mcp_tool_annotations_invalid",
                f"MCP 工具 annotations.{key} 必须是布尔值。",
            )
        result[key] = raw
    return result


def tool_definition_sha256(tool: Mapping) -> str:
    """摘要绑定名称、说明、参数结构与 MCP 自述，供管理员策略防漂移。"""
    payload = {
        "name": tool.get("name", ""),
        "description": tool.get("description", ""),
        "input_schema": tool.get("input_schema", {}),
        "annotations": tool.get("annotations", {}),
    }
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_discovered_tools(tools: Sequence[Mapping]) -> list[dict]:
    """限制第三方服务返回的工具元数据大小与可用字符集合。"""
    if isinstance(tools, (str, bytes)) or len(tools) > _MAX_TOOLS:
        raise MCPConfigError("mcp_tool_catalog_invalid", "MCP 工具列表无效或数量过多。")
    result: list[dict] = []
    names: set[str] = set()
    total = 0
    for raw in tools:
        if not isinstance(raw, Mapping):
            raise MCPConfigError("mcp_tool_catalog_invalid", "MCP 工具元数据无效。")
        name = raw.get("name")
        if not isinstance(name, str) or not _TOOL_NAME_RE.fullmatch(name):
            raise MCPConfigError("mcp_tool_name_invalid", "MCP 工具名称格式无效。")
        if name in names:
            raise MCPConfigError("mcp_tool_name_duplicate", "MCP 服务返回了重复工具名。")
        names.add(name)
        description = " ".join(str(raw.get("description") or "").split())[:2000]
        annotations = normalize_tool_annotations(raw.get("annotations"))
        schema = raw.get("input_schema", raw.get("inputSchema", {}))
        if not isinstance(schema, dict):
            raise MCPConfigError("mcp_tool_schema_invalid", "MCP 工具参数结构无效。")
        _validate_schema_shape(schema)
        try:
            encoded = json.dumps(
                schema, ensure_ascii=False, separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise MCPConfigError(
                "mcp_tool_schema_invalid", "MCP 工具参数结构无法序列化。") from exc
        if len(encoded) > _MAX_TOOL_SCHEMA_BYTES:
            raise MCPConfigError("mcp_tool_schema_too_large", "MCP 工具参数结构过大。")
        annotations_encoded = json.dumps(
            annotations, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        total += (len(encoded) + len(annotations_encoded)
                  + len(name.encode()) + len(description.encode()))
        if total > _MAX_TOOLS_JSON_BYTES:
            raise MCPConfigError("mcp_tool_catalog_too_large", "MCP 工具列表总大小过大。")
        normalized = {
            "name": name,
            "description": description,
            "input_schema": json.loads(encoded),
            "annotations": annotations,
        }
        normalized["definition_sha256"] = tool_definition_sha256(normalized)
        result.append(normalized)
    return result


def redact_discovered_tool_secrets(
    tools: Sequence[Mapping], secret_values: Sequence[str],
) -> list[dict]:
    """在工具元数据落库/进入提示词前清理 MCP 自身凭据。"""
    normalized = normalize_discovered_tools(tools)
    secrets = tuple(value for value in secret_values if value)
    if not secrets:
        return normalized

    def scrub(value):
        if isinstance(value, str):
            if not value:
                return value
            return redact_mcp_error(value, secrets, max_chars=None)
        if isinstance(value, list):
            return [scrub(item) for item in value]
        if isinstance(value, dict):
            return {scrub(key): scrub(item) for key, item in value.items()}
        return value

    cleaned: list[dict] = []
    for tool in normalized:
        if any(secret in tool["name"] for secret in secrets):
            raise MCPConfigError(
                "mcp_tool_name_contains_secret",
                "MCP 工具名称包含该服务的敏感配置值。",
            )
        cleaned.append({
            "name": tool["name"],
            "description": scrub(tool["description"]),
            "input_schema": scrub(tool["input_schema"]),
            "annotations": scrub(tool.get("annotations", {})),
        })
    # 清理后再验证一次，保证 schema 键或值的替换不会让
    # 下游格式化器接收到无效结构。
    return normalize_discovered_tools(cleaned)


def validate_tool_policies(value: Mapping | None) -> dict[str, dict[str, str]]:
    """校验管理员按工具定义设置的风险策略。"""
    if value is None:
        return {}
    if not isinstance(value, Mapping) or len(value) > _MAX_TOOLS:
        raise MCPConfigError(
            "mcp_tool_policies_invalid", "MCP 工具风险策略无效或数量过多。")
    result: dict[str, dict[str, str]] = {}
    for raw_name, raw_policy in value.items():
        if not isinstance(raw_name, str) or not _TOOL_NAME_RE.fullmatch(raw_name):
            raise MCPConfigError(
                "mcp_tool_name_invalid", "MCP 工具风险策略包含无效名称。")
        if not isinstance(raw_policy, Mapping):
            raise MCPConfigError(
                "mcp_tool_policy_invalid", "MCP 工具风险策略格式无效。")
        risk = raw_policy.get("risk")
        digest = raw_policy.get("definition_sha256")
        if risk not in {"low", "medium", "high"}:
            raise MCPConfigError(
                "mcp_tool_risk_invalid", "MCP 工具风险必须是 low、medium 或 high。")
        if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
            raise MCPConfigError(
                "mcp_tool_definition_invalid", "MCP 工具定义摘要无效。")
        result[raw_name] = {
            "risk": risk,
            "definition_sha256": digest,
        }
    return result


def apply_tool_policies(
    tools: Sequence[Mapping], policies: Mapping | None,
) -> list[dict]:
    """计算有效风险；无策略或定义漂移时始终回退到 HIGH。"""
    try:
        checked_policies = validate_tool_policies(policies)
    except MCPConfigError:
        checked_policies = {}
    result: list[dict] = []
    for raw_tool in tools:
        if not isinstance(raw_tool, Mapping):
            continue
        tool = dict(raw_tool)
        name = tool.get("name")
        digest = tool.get("definition_sha256")
        policy = checked_policies.get(name) if isinstance(name, str) else None
        active = bool(
            policy and isinstance(digest, str)
            and policy["definition_sha256"] == digest
        )
        tool["effective_risk"] = policy["risk"] if active else "high"
        tool["risk_source"] = "administrator" if active else "platform_default"
        tool["policy_status"] = (
            "active" if active else "stale" if policy else "default"
        )
        result.append(tool)
    return result


class MCPSecretEnvironmentStore:
    """以随机文件引用保存整组秘密环境变量。"""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory).expanduser().absolute()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.directory.is_symlink() or not self.directory.is_dir():
            raise MCPConfigError(
                "mcp_secret_directory_invalid", "MCP 凭据目录不能是符号链接。")
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        info = self.directory.stat()
        if ((hasattr(os, "geteuid") and info.st_uid != os.geteuid())
                or stat.S_IMODE(info.st_mode) & 0o077):
            raise MCPConfigError(
                "mcp_secret_directory_permissions",
                "MCP 凭据目录必须归当前控制面账户所有且权限为 0700。",
            )

    def _path(self, secret_ref: str) -> Path:
        if not _SECRET_REF_RE.fullmatch(secret_ref):
            raise MCPConfigError(
                "mcp_secret_reference_invalid", "MCP 凭据引用无效。")
        return self.directory / secret_ref

    def write(self, values: Mapping[str, str]) -> str:
        cleaned = validate_environment(values, secret=True)
        if not cleaned:
            return ""
        data = json.dumps(
            cleaned, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        secret_ref = uuid.uuid4().hex
        path = self._path(secret_ref)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view):]
            os.fsync(fd)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise MCPConfigError(
                    "mcp_secret_storage_invalid", "MCP 凭据存储不是普通文件。")
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            os.close(fd)
        return secret_ref

    def read(self, secret_ref: str) -> dict[str, str]:
        if not secret_ref:
            return {}
        path = self._path(secret_ref)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except FileNotFoundError as exc:
            raise MCPConfigError(
                "mcp_secret_unavailable", "已保存的 MCP 凭据不可用，请重新输入。",
            ) from exc
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) & 0o077):
                raise MCPConfigError(
                    "mcp_secret_permissions", "MCP 凭据文件权限不安全。")
            data = os.read(fd, _MAX_ENV_TOTAL_BYTES * 2 + 1)
            if len(data) > _MAX_ENV_TOTAL_BYTES * 2:
                raise MCPConfigError("mcp_secret_too_large", "MCP 凭据文件过大。")
        finally:
            os.close(fd)
        try:
            payload = json.loads(data)
        except (UnicodeDecodeError, ValueError) as exc:
            raise MCPConfigError("mcp_secret_invalid", "MCP 凭据文件内容无效。") from exc
        return validate_environment(payload, secret=True)

    def delete(self, secret_ref: str) -> None:
        if not secret_ref:
            return
        try:
            self._path(secret_ref).unlink(missing_ok=True)
        except OSError:
            pass

    def cleanup(self, live_refs: set[str]) -> None:
        for path in self.directory.iterdir():
            if path.name not in live_refs and _SECRET_REF_RE.fullmatch(path.name):
                try:
                    path.unlink()
                except OSError:
                    pass


class MCPConfigStore:
    """自定义 MCP 服务的线程安全 SQLite 数据层。"""

    def __init__(self, db_path: str, secrets_dir: str | Path | None = None):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        columns = {
            row[1] for row in self._conn.execute(
                "PRAGMA table_info(custom_mcp_servers)"
            ).fetchall()
        }
        if "cwd" not in columns:
            self._conn.execute(
                "ALTER TABLE custom_mcp_servers "
                "ADD COLUMN cwd TEXT NOT NULL DEFAULT ''"
            )
        if "tool_policies_json" not in columns:
            self._conn.execute(
                "ALTER TABLE custom_mcp_servers "
                "ADD COLUMN tool_policies_json TEXT NOT NULL DEFAULT '{}'"
            )
        # 旧版本没有 cwd；迁移时保存命令目录的解析路径。配置即使已经因
        # 外部文件变化而失效，也不阻止控制面启动，runtime_config 会在真正
        # 启动代码前重新执行严格存在性与非符号链接校验。
        for row in self._conn.execute(
            "SELECT id, command FROM custom_mcp_servers WHERE cwd=''"
        ).fetchall():
            try:
                cwd = str(Path(row["command"]).parent.resolve(strict=False))
            except (OSError, RuntimeError, ValueError):
                cwd = os.path.abspath(os.path.dirname(row["command"]) or os.curdir)
            self._conn.execute(
                "UPDATE custom_mcp_servers SET cwd=? WHERE id=?",
                (cwd, row["id"]),
            )
        self._conn.commit()
        self._lock = threading.RLock()
        if secrets_dir is None:
            secrets_dir = default_mcp_secrets_directory(db_path)
        self.secrets = MCPSecretEnvironmentStore(secrets_dir)
        refs = {
            row[0] for row in self._conn.execute(
                "SELECT secret_env_ref FROM custom_mcp_servers "
                "WHERE secret_env_ref <> ''"
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

    @staticmethod
    def _json_object(value: str, fallback):
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if isinstance(parsed, type(fallback)) else fallback

    @classmethod
    def _stored_tools(cls, value: str) -> list[dict]:
        """重新规范化落库目录；旧数据补摘要，畸形/篡改数据 fail closed。"""
        raw = cls._json_object(value, [])
        try:
            return normalize_discovered_tools(raw)
        except MCPConfigError:
            return []

    def _public_locked(self, row: sqlite3.Row) -> dict:
        args = self._json_object(row["args_json"], [])
        env = self._json_object(row["env_json"], {})
        secret_keys = self._json_object(row["secret_env_keys_json"], [])
        tools = self._stored_tools(row["tools_json"])
        raw_tool_policies = self._json_object(row["tool_policies_json"], {})
        try:
            tool_policies = validate_tool_policies(raw_tool_policies)
        except MCPConfigError:
            tool_policies = {}
        return {
            "id": row["id"],
            "name": row["name"],
            "transport": row["transport"],
            "command": row["command"],
            "cwd": row["cwd"],
            "args": args,
            "env": env,
            "secret_env_keys": secret_keys,
            "enabled": bool(row["enabled"]),
            "version": row["version"],
            "status": row["status"],
            "tool_count": len(tools),
            "tools": apply_tool_policies(tools, tool_policies),
            "tool_policies": tool_policies,
            "error": row["error"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "updated_by": row["updated_by"],
            "last_tested_at": row["last_tested_at"],
            "last_test_ok": (None if row["last_test_ok"] is None
                             else bool(row["last_test_ok"])),
        }

    def list_servers(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM custom_mcp_servers ORDER BY created_at, id"
            ).fetchall()
            return [self._public_locked(row) for row in rows]

    def get_server(self, server_id: str) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            if row is None:
                raise MCPConfigError(
                    "mcp_server_not_found", "MCP 服务不存在。", status_code=404)
            return self._public_locked(row)

    def create_server(
        self,
        *,
        server_id: str,
        name: str,
        command: str,
        cwd: str | None = None,
        args: Sequence[str] | None = None,
        env: Mapping[str, str] | None = None,
        secret_env: Mapping[str, str] | None = None,
        enabled: bool = False,
        updated_by: str = "",
        audit=None,
    ) -> dict:
        submitted_secrets = validate_environment(
            secret_env, secret=True, allow_blank_secret=True)
        config = make_stdio_server_config(
            server_id=server_id, name=name, command=command, cwd=cwd, args=args,
            env=env, secret_env={
                key: value for key, value in submitted_secrets.items() if value
            }, enabled=enabled,
        )
        secret_ref = self.secrets.write(config.secret_env)
        now = time.time()
        try:
            with self.transaction():
                try:
                    self._conn.execute(
                        "INSERT INTO custom_mcp_servers(id, name, command, cwd, "
                        "args_json, env_json, secret_env_ref, "
                        "secret_env_keys_json, enabled, version, created_at, "
                        "updated_at, updated_by) "
                        "VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?)",
                        (config.id, config.name, config.command, config.cwd,
                         json.dumps(list(config.args), ensure_ascii=False),
                         json.dumps(config.env, ensure_ascii=False), secret_ref,
                         json.dumps(sorted(config.secret_env), ensure_ascii=False),
                         int(config.enabled), now, now, updated_by),
                    )
                except sqlite3.IntegrityError as exc:
                    raise MCPConfigError(
                        "mcp_server_exists", "该 MCP 服务 ID 已存在。",
                        status_code=409,
                    ) from exc
                row = self._conn.execute(
                    "SELECT * FROM custom_mcp_servers WHERE id=?", (config.id,),
                ).fetchone()
                assert row is not None
                result = self._public_locked(row)
                if audit is not None:
                    audit(result, self._conn)
                return result
        except Exception:
            self.secrets.delete(secret_ref)
            raise

    def update_server(
        self,
        server_id: str,
        *,
        expected_version: int,
        name: str,
        command: str,
        cwd: str | None = None,
        args: Sequence[str] | None,
        env: Mapping[str, str] | None,
        enabled: bool,
        secret_env: Mapping[str, str] | None = None,
        clear_secret_env_keys: Sequence[str] | None = None,
        updated_by: str = "",
        audit=None,
    ) -> dict:
        validate_server_id(server_id)
        regular = validate_environment(env, secret=False)
        updates = validate_environment(
            secret_env, secret=True, allow_blank_secret=True)
        if (clear_secret_env_keys is not None
                and (isinstance(clear_secret_env_keys, (str, bytes))
                     or not isinstance(clear_secret_env_keys, Sequence))):
            raise MCPConfigError(
                "mcp_secret_clear_invalid", "待清除的秘密环境变量必须是字符串数组。")
        if (clear_secret_env_keys is not None
                and len(clear_secret_env_keys) > _MAX_ENV_ITEMS):
            raise MCPConfigError(
                "mcp_secret_clear_invalid", "待清除的秘密环境变量数量过多。")
        clear_keys = {_validate_env_key(key)
                      for key in (clear_secret_env_keys or [])}
        if clear_keys & {key for key, value in updates.items() if value}:
            raise MCPConfigError(
                "mcp_secret_update_conflict",
                "同一秘密环境变量不能同时更新和清除。",
            )
        new_ref = ""
        old_ref = ""
        changed_secret_file = False
        result = None
        try:
            with self.transaction():
                row = self._conn.execute(
                    "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
                ).fetchone()
                if row is None:
                    raise MCPConfigError(
                        "mcp_server_not_found", "MCP 服务不存在。", status_code=404)
                if row["version"] != expected_version:
                    raise MCPConfigVersionConflict()
                old_ref = row["secret_env_ref"]
                current_secrets = self.secrets.read(old_ref)
                for key in clear_keys:
                    current_secrets.pop(key, None)
                for key, value in updates.items():
                    if value:
                        current_secrets[key] = value
                overlap = set(regular) & set(current_secrets)
                if overlap:
                    raise MCPConfigError(
                        "mcp_env_key_conflict",
                        "环境变量不能同时是普通值和秘密值："
                        f"{sorted(overlap)[0]}",
                    )
                config = make_stdio_server_config(
                    server_id=server_id, name=name, command=command, cwd=cwd,
                    args=args,
                    env=regular, secret_env=current_secrets, enabled=enabled,
                    version=expected_version + 1,
                )
                old_secrets = self.secrets.read(old_ref)
                changed_secret_file = current_secrets != old_secrets
                if changed_secret_file:
                    new_ref = self.secrets.write(current_secrets)
                    target_ref = new_ref
                else:
                    target_ref = old_ref
                now = time.time()
                self._conn.execute(
                    "UPDATE custom_mcp_servers SET name=?, command=?, cwd=?, "
                    "args_json=?, env_json=?, secret_env_ref=?, "
                    "secret_env_keys_json=?, enabled=?, version=version+1, "
                    "status='untested', tools_json='[]', tool_policies_json='{}', "
                    "tool_count=0, "
                    "error='', updated_at=?, updated_by=?, last_tested_at=NULL, "
                    "last_test_ok=NULL WHERE id=?",
                    (config.name, config.command, config.cwd,
                     json.dumps(list(config.args), ensure_ascii=False),
                     json.dumps(config.env, ensure_ascii=False), target_ref,
                     json.dumps(sorted(config.secret_env), ensure_ascii=False),
                     int(config.enabled), now, updated_by, server_id),
                )
                row = self._conn.execute(
                    "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
                ).fetchone()
                assert row is not None
                result = self._public_locked(row)
                if audit is not None:
                    audit(result, self._conn)
        except Exception:
            self.secrets.delete(new_ref)
            raise
        if changed_secret_file:
            self.secrets.delete(old_ref)
        assert result is not None
        return result

    def set_enabled(
        self, server_id: str, *, expected_version: int, enabled: bool,
        updated_by: str = "", audit=None,
    ) -> dict:
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            if row is None:
                raise MCPConfigError(
                    "mcp_server_not_found", "MCP 服务不存在。", status_code=404)
            if row["version"] != expected_version:
                raise MCPConfigVersionConflict()
            self._conn.execute(
                "UPDATE custom_mcp_servers SET enabled=?, version=version+1, "
                "updated_at=?, updated_by=? WHERE id=?",
                (int(enabled), time.time(), updated_by, server_id),
            )
            fresh = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            assert fresh is not None
            result = self._public_locked(fresh)
            if audit is not None:
                audit(result, self._conn)
            return result

    def set_tool_policies(
        self,
        server_id: str,
        *,
        expected_version: int,
        policies: Mapping | None,
        updated_by: str = "",
        audit=None,
    ) -> dict:
        """保存管理员分级；只接受当前已发现工具的精确定义摘要。"""
        validate_server_id(server_id)
        checked = validate_tool_policies(policies)
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            if row is None:
                raise MCPConfigError(
                    "mcp_server_not_found", "MCP 服务不存在。", status_code=404)
            if row["version"] != expected_version:
                raise MCPConfigVersionConflict()
            if bool(row["enabled"]):
                raise MCPConfigError(
                    "mcp_disable_before_tool_policy",
                    "请先停用 MCP 服务，再修改工具风险分级。",
                    status_code=409,
                )
            if checked:
                tools = self._stored_tools(row["tools_json"])
                if row["status"] != "connected" or not tools:
                    raise MCPConfigError(
                        "mcp_tools_not_discovered",
                        "请先测试 MCP 连接并获取工具清单，再设置风险分级。",
                        status_code=409,
                    )
                definitions = {
                    tool.get("name"): tool.get("definition_sha256")
                    for tool in tools if isinstance(tool, dict)
                }
                for name, policy in checked.items():
                    current_digest = definitions.get(name)
                    if current_digest is None:
                        raise MCPConfigError(
                            "mcp_tool_not_found", f"MCP 工具 {name} 不存在。",
                            status_code=404,
                        )
                    if current_digest != policy["definition_sha256"]:
                        raise MCPConfigError(
                            "mcp_tool_definition_conflict",
                            f"MCP 工具 {name} 的定义已变化，请刷新后重新评估。",
                            status_code=409,
                        )
            self._conn.execute(
                "UPDATE custom_mcp_servers SET tool_policies_json=?, "
                "version=version+1, updated_at=?, updated_by=? WHERE id=?",
                (json.dumps(checked, ensure_ascii=False, sort_keys=True),
                 time.time(), updated_by, server_id),
            )
            fresh = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            assert fresh is not None
            result = self._public_locked(fresh)
            if audit is not None:
                audit(result, self._conn)
            return result

    def delete_server(
        self, server_id: str, *, expected_version: int | None = None,
        audit=None,
    ) -> None:
        secret_ref = ""
        with self.transaction():
            row = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            if row is None:
                raise MCPConfigError(
                    "mcp_server_not_found", "MCP 服务不存在。", status_code=404)
            if expected_version is not None and row["version"] != expected_version:
                raise MCPConfigVersionConflict()
            secret_ref = row["secret_env_ref"]
            public = self._public_locked(row)
            self._conn.execute(
                "DELETE FROM custom_mcp_servers WHERE id=?", (server_id,))
            if audit is not None:
                audit(public, self._conn)
        self.secrets.delete(secret_ref)

    def runtime_config(
        self, server_id: str, *, expected_version: int | None = None,
    ) -> MCPServerConfig:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM custom_mcp_servers WHERE id=?", (server_id,),
            ).fetchone()
            if row is None:
                raise MCPConfigError(
                    "mcp_server_not_found", "MCP 服务不存在。", status_code=404)
            if expected_version is not None and row["version"] != expected_version:
                raise MCPConfigVersionConflict()
            secrets = self.secrets.read(row["secret_env_ref"])
            policies = self._json_object(row["tool_policies_json"], {})
            try:
                checked_policies = validate_tool_policies(policies)
            except MCPConfigError:
                # 离线篡改或旧格式策略一律失效；真正运行时保持最高风险。
                checked_policies = {}
            return make_stdio_server_config(
                server_id=row["id"], name=row["name"], command=row["command"],
                cwd=row["cwd"],
                args=self._json_object(row["args_json"], []),
                env=self._json_object(row["env_json"], {}),
                secret_env=secrets, enabled=bool(row["enabled"]),
                tool_policies=checked_policies,
                version=row["version"],
            )

    def enabled_runtime_configs(self) -> list[MCPServerConfig]:
        with self._lock:
            ids = [row[0] for row in self._conn.execute(
                "SELECT id FROM custom_mcp_servers WHERE enabled=1 "
                "ORDER BY created_at, id"
            ).fetchall()]
            return [self.runtime_config(server_id) for server_id in ids]

    def record_test(
        self,
        server_id: str,
        *,
        ok: bool,
        tools: Sequence[Mapping] | None = None,
        error: str = "",
        expected_version: int | None = None,
    ) -> bool:
        cleaned_tools = normalize_discovered_tools(tools or []) if ok else []
        safe_error = "" if ok else redact_mcp_error(error)
        with self.transaction():
            query = (
                "UPDATE custom_mcp_servers SET status=?, tools_json=?, "
                "tool_count=?, error=?, last_tested_at=?, last_test_ok=? "
                "WHERE id=?"
            )
            params: list = [
                "connected" if ok else "error",
                json.dumps(cleaned_tools, ensure_ascii=False),
                len(cleaned_tools), safe_error, time.time(), int(ok), server_id,
            ]
            if expected_version is not None:
                query += " AND version=?"
                params.append(expected_version)
            cursor = self._conn.execute(query, params)
            return cursor.rowcount == 1

    def close(self) -> None:
        with self._lock:
            self._conn.close()
