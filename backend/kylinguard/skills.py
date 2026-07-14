"""Skill 目录、元数据与每轮规划上下文的安全装配。

Skill 是声明式工作流，不是新的执行通道。这里仅加载 ``SKILL.md`` 并生成
给规划器使用的低优先级指导；文件中的脚本或命令永远不会由本模块直接运行。

目录布局保持与常见 Agent Skill 兼容：每个一级子目录包含一个 ``SKILL.md``。
当前加载器不会自动注入或执行 ``references/``、``scripts/`` 等配套资源。内置
目录只读，用户目录可通过本类提供的 CRUD 接口管理，二者使用同一份启停状态。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import threading
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from kylinguard.mcp_config import MCP_TOOL_NAME_PATTERN


MAX_SKILL_BYTES = 128 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_SKILLS = 256
MAX_TOOL_ITEMS = 128
MAX_SKILL_ROUTING_CHARS = 8000
MAX_SKILLS_PER_TURN = 4

_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
_QUALIFIED_TOOL_PATTERN = (
    rf"[a-z][a-z0-9_-]{{0,63}}\.{MCP_TOOL_NAME_PATTERN}"
)
_TOOL_RE = re.compile(rf"^{_QUALIFIED_TOOL_PATTERN}$")
_CATALOG_ENTRY_RE = re.compile(
    rf"(?ms)^-\s+(?P<name>{_QUALIFIED_TOOL_PATTERN})"
    r"(?P<body>.*?)(?=^-\s+|\Z)"
)


class SkillError(RuntimeError):
    """Skill 管理的公共基类。"""


class SkillNotFoundError(SkillError):
    """指定 Skill 不存在。"""


class SkillDisabledError(SkillError):
    """指定 Skill 当前未启用。"""


class SkillValidationError(SkillError):
    """SKILL.md 或 Skill 标识不满足安全约束。"""


class SkillConflictError(SkillError):
    """用户写操作与现有 Skill 冲突。"""


class SkillSummary(BaseModel):
    """适合列表/API 返回且不包含工作流正文的 Skill 元数据。"""

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    description: str
    version: str
    # 可选依赖只用于检查当前 MCP 工具是否存在，不限制、更不授权工具调用。
    required_tools: tuple[str, ...] = ()
    # 兼容旧 SKILL.md；自动路由上线后该字段不再限制选择方式。
    manual_only: bool = False
    enabled: bool = True
    sha256: str
    source: Literal["builtin", "user"]
    relative_path: str


class SkillDefinition(SkillSummary):
    """单轮冻结的 Skill 快照。"""

    instructions: str
    content: str

    def prompt_data(self) -> dict:
        """返回进入规划提示的最小冻结快照。"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "instructions": self.instructions,
        }

    def prompt_payload(self) -> str:
        """JSON 编码正文，避免正文伪造提示词分隔符或属性。"""
        payload = json.dumps(
            self.prompt_data(), ensure_ascii=False, separators=(",", ":"),
        )
        # JSON 字符串自身允许 ``<``，因此额外使用 JSON 等价的 Unicode
        # 转义，确保正文无法在物理提示文本中伪造 XML 风格结束标记。
        return (payload.replace("&", r"\u0026")
                .replace("<", r"\u003c")
                .replace(">", r"\u003e"))

    def audit_payload(self) -> dict:
        """审计只记录可重放标识与依赖，不落盘 Skill 正文。"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "sha256": self.sha256,
            "source": self.source,
            "manual_only": self.manual_only,
            "required_tools": list(self.required_tools),
        }


def build_skills_prompt_payload(skills: tuple[SkillDefinition, ...]) -> str:
    """按管理员选择顺序编码一组冻结 Skill，不执行或改写正文。"""
    payload = json.dumps(
        {"skills": [skill.prompt_data() for skill in skills]},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (payload.replace("&", r"\u0026")
            .replace("<", r"\u003c")
            .replace(">", r"\u003e"))


def normalize_selected_skill_ids(
    skill_ids: list[str] | tuple[str, ...] | None,
    *,
    legacy_skill_id: str = "",
) -> tuple[str, ...]:
    """归一化新旧请求字段；新字段存在时旧字段只能是其中同一项。"""
    raw_ids = list(skill_ids or [])
    legacy = str(legacy_skill_id or "").strip()
    if len(raw_ids) > MAX_SKILLS_PER_TURN:
        raise SkillValidationError(
            f"一轮最多选择 {MAX_SKILLS_PER_TURN} 个 Skill。"
        )
    if raw_ids and legacy and legacy not in {
        str(item or "").strip() for item in raw_ids
    }:
        raise SkillValidationError(
            "skill_id 与 skill_ids 指定了不同的 Skill。"
        )
    if not raw_ids and legacy:
        raw_ids = [legacy]

    normalized: list[str] = []
    for raw in raw_ids:
        if not isinstance(raw, str):
            raise SkillValidationError("skill_ids 必须是 Skill ID 字符串数组。")
        skill_id = _validate_id(raw)
        if skill_id not in normalized:
            normalized.append(skill_id)
    return tuple(normalized)


def collect_skill_dependencies(
    skills: tuple[SkillDefinition, ...],
) -> tuple[str, ...]:
    """按选择顺序合并可选工具依赖；依赖不改变工具或权限范围。"""
    required: list[str] = []
    for skill in skills:
        for tool in skill.required_tools:
            if tool not in required:
                required.append(tool)
    return tuple(required)


class SkillLoadIssue(BaseModel):
    """列表扫描时的单项错误；坏文件不会拖垮整个扩展页。"""

    model_config = ConfigDict(frozen=True)

    id: str
    source: Literal["builtin", "user"]
    message: str


def builtin_skills_dir() -> Path:
    """随 Python 包分发的只读内置 Skill 目录。"""
    return Path(__file__).with_name("builtin_skills")


def _validate_id(skill_id: str) -> str:
    value = str(skill_id or "").strip()
    if not _ID_RE.fullmatch(value) or value in {".", ".."}:
        raise SkillValidationError(
            "Skill ID 仅允许 1-64 位小写字母、数字、点、下划线和短横线，"
            "且必须以字母或数字开头、结尾。"
        )
    return value


def _read_regular_file(path: Path, *, limit: int) -> bytes:
    """以 no-follow 方式读取普通文件并限制大小，避免路径替换与设备文件。"""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except (OSError, ValueError) as exc:
        raise SkillValidationError(f"无法安全读取 {path.name}。") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise SkillValidationError(f"{path.name} 必须是普通文件。")
        if info.st_size > limit:
            raise SkillValidationError(
                f"{path.name} 超过大小上限 {limit} 字节。"
            )
        chunks: list[bytes] = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise SkillValidationError(
                f"{path.name} 超过大小上限 {limit} 字节。"
            )
        return data
    finally:
        os.close(fd)


def _scalar(raw: str):
    value = raw.strip()
    if not value:
        return ""
    lower = value.lower()
    if lower in {"true", "false"}:
        return lower == "true"
    if lower in {"null", "~"}:
        return None
    if value[0:1] in {'"', "'"}:
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise SkillValidationError("frontmatter 字符串引号不完整。") from exc
        if not isinstance(parsed, str):
            raise SkillValidationError("frontmatter 引号值必须是字符串。")
        return parsed
    if value.startswith("["):
        if not value.endswith("]"):
            raise SkillValidationError("frontmatter 行内列表缺少右方括号。")
        inner = value[1:-1].strip()
        if not inner:
            return []
        # JSON 是首选；为常见 YAML ``[a, b]`` 形式提供受限降级解析。
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [_scalar(item.strip()) for item in inner.split(",")]
        if not isinstance(parsed, list):
            raise SkillValidationError("frontmatter 列表格式不合法。")
        return parsed
    return value


def _parse_frontmatter(header: str) -> dict:
    """解析 Skill 所需的 YAML 子集，不加载对象标签或执行构造器。"""
    lines = header.splitlines()
    values: dict[str, object] = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#"):
            index += 1
            continue
        if line[:1].isspace() or ":" not in line:
            raise SkillValidationError(
                f"frontmatter 第 {index + 1} 行必须是顶层 key: value。"
            )
        key, raw = line.split(":", 1)
        key = key.strip()
        # Agent Skills/Claude 等常见扩展字段会使用短横线（如
        # ``allowed-tools``、``argument-hint``）；未知字段只解析为数据并忽略。
        if not re.fullmatch(r"[a-z_][a-z0-9_-]*", key):
            raise SkillValidationError(f"frontmatter 字段名 {key!r} 不合法。")
        if key in values:
            raise SkillValidationError(f"frontmatter 字段 {key!r} 重复。")
        raw = raw.strip()
        if raw in {"|", "|-", "|+", ">", ">-", ">+"}:
            folded = raw.startswith(">")
            block: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                if current and not current[:1].isspace():
                    break
                block.append(current[2:] if current.startswith("  ")
                             else current.lstrip(" \t"))
                index += 1
            if folded:
                paragraphs = "\n".join(block).split("\n\n")
                values[key] = "\n\n".join(
                    " ".join(part.splitlines()) for part in paragraphs
                ).strip()
            else:
                values[key] = "\n".join(block).strip("\n")
            continue
        if not raw:
            block: list[str] = []
            index += 1
            while index < len(lines):
                current = lines[index]
                if current and not current[:1].isspace():
                    break
                block.append(current)
                index += 1
            if key == "required_tools":
                items: list[object] = []
                for current in block:
                    if not current.strip():
                        continue
                    match = re.fullmatch(r"\s+-\s+(.+)", current)
                    if not match:
                        raise SkillValidationError(
                            "frontmatter 字段 'required_tools' 必须是字符串列表。"
                        )
                    items.append(_scalar(match.group(1)))
                values[key] = items
            else:
                # 未知的列表/映射等嵌套元数据（例如 ``metadata``）不参与
                # 运行时语义；完整跳过其缩进块即可兼容标准第三方 Skill。
                values[key] = None
            continue
        values[key] = _scalar(raw)
        index += 1
    return values


def _split_document(text: str) -> tuple[dict, str]:
    normalized = text.lstrip("\ufeff")
    lines = normalized.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError("SKILL.md 必须以 YAML frontmatter（---）开头。")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1)
         if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise SkillValidationError("SKILL.md 的 frontmatter 缺少结束分隔符 ---。")
    header = "\n".join(lines[1:closing])
    instructions = "\n".join(lines[closing + 1:]).strip()
    if not instructions:
        raise SkillValidationError("SKILL.md 必须包含非空工作流正文。")
    return _parse_frontmatter(header), instructions


def _string_field(metadata: dict, key: str, *, required: bool,
                  max_length: int, default: str = "") -> str:
    value = metadata.get(key, default)
    if not isinstance(value, str):
        raise SkillValidationError(f"frontmatter 字段 {key!r} 必须是字符串。")
    value = value.strip()
    if required and not value:
        raise SkillValidationError(f"frontmatter 缺少非空字段 {key!r}。")
    if len(value) > max_length or "\x00" in value:
        raise SkillValidationError(f"frontmatter 字段 {key!r} 过长或包含 NUL。")
    return value


def _bool_field(metadata: dict, key: str, default: bool) -> bool:
    value = metadata.get(key, default)
    if not isinstance(value, bool):
        raise SkillValidationError(f"frontmatter 字段 {key!r} 必须是布尔值。")
    return value


def _tool_list(metadata: dict, key: str) -> tuple[str, ...]:
    raw = metadata.get(key, [])
    if raw is None:
        raw = []
    if not isinstance(raw, list) or len(raw) > MAX_TOOL_ITEMS:
        raise SkillValidationError(
            f"frontmatter 字段 {key!r} 必须是不超过 {MAX_TOOL_ITEMS} 项的列表。"
        )
    result: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not _TOOL_RE.fullmatch(item.strip()):
            raise SkillValidationError(
                f"frontmatter 字段 {key!r} 包含无效的 server.tool 名称。"
            )
        normalized = item.strip()
        if normalized not in result:
            result.append(normalized)
    return tuple(result)


def parse_skill_document(
    skill_id: str,
    content: str | bytes,
    *,
    source: Literal["builtin", "user"] = "user",
    enabled_override: bool | None = None,
) -> SkillDefinition:
    """解析并验证完整 SKILL.md；供存储与 API 写入前共同复用。"""
    skill_id = _validate_id(skill_id)
    if isinstance(content, str):
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SkillValidationError("SKILL.md 必须是有效 UTF-8 文本。") from exc
    elif isinstance(content, bytes):
        raw = content
    else:
        raise SkillValidationError("SKILL.md 内容必须是字符串或字节。")
    if len(raw) > MAX_SKILL_BYTES:
        raise SkillValidationError(
            f"SKILL.md 超过大小上限 {MAX_SKILL_BYTES} 字节。"
        )
    if b"\x00" in raw:
        raise SkillValidationError("SKILL.md 不允许包含 NUL。")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SkillValidationError("SKILL.md 必须是有效 UTF-8 文本。") from exc
    metadata, instructions = _split_document(text)
    # 未知字段可用于未来兼容，但必须已经由安全子集解析器验证；当前不执行。
    # 这里不因未知字段拒绝整份 Skill，以便兼容上游的 display_name、license
    # 等纯元数据；所有值仍已被受限解析器收敛为标量或标量列表。
    name = _string_field(metadata, "name", required=True, max_length=100)
    description = _string_field(
        metadata, "description", required=True, max_length=1024,
    )
    version = _string_field(
        metadata, "version", required=False, max_length=64, default="1.0.0",
    ) or "1.0.0"
    required_tools = _tool_list(metadata, "required_tools")
    default_enabled = _bool_field(metadata, "enabled", True)
    enabled = default_enabled if enabled_override is None else enabled_override
    # 旧版本要求 manual_only=true。现在保留并回显该字段以兼容存量文件，
    # 但路由不再消费它；新文档可以完全省略。
    manual_only = _bool_field(metadata, "manual_only", False)
    return SkillDefinition(
        id=skill_id,
        name=name,
        description=description,
        version=version,
        required_tools=required_tools,
        manual_only=manual_only,
        enabled=enabled,
        sha256=hashlib.sha256(raw).hexdigest(),
        source=source,
        relative_path=f"{skill_id}/SKILL.md",
        instructions=instructions,
        content=text,
    )


def catalog_tool_names(catalog: str) -> frozenset[str]:
    """从 ToolManager 的受控目录文本中提取精确限定名。"""
    return frozenset(
        match.group("name") for match in _CATALOG_ENTRY_RE.finditer(catalog)
    )


def build_skill_routing_catalog(
    summaries: list[SkillSummary],
    *,
    query: str = "",
    max_chars: int = MAX_SKILL_ROUTING_CHARS,
) -> tuple[str, dict]:
    """渐进披露 Skill 摘要，并严格限制初始规划提示的字符预算。"""
    included: list[dict[str, str]] = []
    # 超出预算时优先放入与管理员原始消息字符重合更多的摘要，避免固定按
    # ID 排序导致后半部分 Skill 永远没有自动匹配机会。这里只做候选排序；
    # 正常规划模型负责选择，后端仍会校验 ID 并冻结正文快照。
    query_chars = {
        char for char in str(query or "").casefold() if char.isalnum()
    }

    def relevance(item: SkillSummary) -> tuple[int, str]:
        metadata_chars = set(
            f"{item.id} {item.name} {item.description}".casefold()
        )
        return (-len(query_chars & metadata_chars), item.id)

    ordered = sorted(summaries, key=relevance)
    for summary in ordered:
        candidate = {
            "id": summary.id,
            "name": summary.name,
            "description": summary.description,
        }
        trial = json.dumps(
            {"skills": [*included, candidate]},
            ensure_ascii=False, separators=(",", ":"),
        )
        if len(trial) > max_chars:
            continue
        included.append(candidate)
    payload = json.dumps(
        {"skills": included}, ensure_ascii=False, separators=(",", ":"),
    )
    return payload, {
        "candidate_count": len(ordered),
        "included_count": len(included),
        "truncated": len(included) < len(ordered),
        "chars": len(payload),
    }


class SkillStore:
    """合并只读内置目录与可写用户目录的 Skill 存储。"""

    def __init__(
        self,
        builtin_dir: str | os.PathLike | None = None,
        user_dir: str | os.PathLike | None = None,
        state_path: str | os.PathLike | None = None,
    ):
        self.builtin_dir = Path(
            builtin_dir if builtin_dir is not None else builtin_skills_dir()
        ).expanduser().resolve()
        self.user_dir = (
            Path(user_dir).expanduser().resolve() if user_dir is not None
            else None
        )
        if state_path is None:
            self.state_path = None
        else:
            # 固定父目录但不解析最终文件名；若攻击者预先放置同名符号链接，
            # ``_read_regular_file`` 会因 O_NOFOLLOW 拒绝，而保存时原子替换
            # 链接本身，不会沿链接覆盖目录外文件。
            candidate = Path(state_path).expanduser()
            self.state_path = candidate.parent.resolve() / candidate.name
        self._lock = threading.RLock()
        self._issues: tuple[SkillLoadIssue, ...] = ()
        self._enabled_overrides = self._load_state()

    @property
    def state_persistent(self) -> bool:
        """调用方可据此判断启停是否会跨进程保留。"""
        return self.state_path is not None

    def _load_state(self) -> dict[str, bool]:
        if self.state_path is None or not self.state_path.exists():
            return {}
        raw = _read_regular_file(self.state_path, limit=MAX_STATE_BYTES)
        try:
            payload = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SkillValidationError("Skill 启停状态文件不是有效 JSON。") from exc
        enabled = payload.get("enabled", {}) if isinstance(payload, dict) else None
        if not isinstance(enabled, dict):
            raise SkillValidationError("Skill 启停状态文件结构不合法。")
        result: dict[str, bool] = {}
        for key, value in enabled.items():
            skill_id = _validate_id(key)
            if not isinstance(value, bool):
                raise SkillValidationError("Skill 启停状态必须是布尔值。")
            result[skill_id] = value
        return result

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({
            "version": 1,
            "enabled": dict(sorted(self._enabled_overrides.items())),
        }, ensure_ascii=False, sort_keys=True).encode("utf-8")
        fd, temporary = tempfile.mkstemp(
            prefix=f".{self.state_path.name}.",
            dir=self.state_path.parent,
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    @staticmethod
    def _iter_ids(root: Path | None) -> list[str]:
        if root is None or not root.exists():
            return []
        if not root.is_dir():
            raise SkillValidationError(f"Skill 根路径 {root} 不是目录。")
        result: list[str] = []
        with os.scandir(root) as entries:
            for entry in entries:
                if (entry.is_dir(follow_symlinks=False)
                        and _ID_RE.fullmatch(entry.name)):
                    result.append(entry.name)
        return sorted(result)

    def _source_for(self, skill_id: str) -> tuple[Path, Literal["builtin", "user"]]:
        skill_id = _validate_id(skill_id)
        builtin = self.builtin_dir / skill_id
        if builtin.is_dir() and not builtin.is_symlink():
            return builtin, "builtin"
        if self.user_dir is not None:
            user = self.user_dir / skill_id
            if user.is_dir() and not user.is_symlink():
                return user, "user"
        raise SkillNotFoundError(f"Skill {skill_id!r} 不存在。")

    def _load(self, skill_id: str) -> SkillDefinition:
        directory, source = self._source_for(skill_id)
        skill_file = directory / "SKILL.md"
        raw = _read_regular_file(skill_file, limit=MAX_SKILL_BYTES)
        if skill_id in self._enabled_overrides:
            enabled_override = self._enabled_overrides[skill_id]
        else:
            # 用户目录既是 GUI 存储位置，也可能被管理员或安装器直接写入。
            # 未经 KylinGuard 显式启用的第三方内容一律默认停用；内置 Skill
            # 仍使用随包 frontmatter 的默认状态。
            enabled_override = False if source == "user" else None
        return parse_skill_document(
            skill_id,
            raw,
            source=source,
            enabled_override=enabled_override,
        )

    def list_skills(self) -> list[SkillSummary]:
        """列出可加载项；单个损坏项通过 ``issues()`` 报告而非全局失败。"""
        with self._lock:
            builtin_ids = self._iter_ids(self.builtin_dir)
            user_ids = self._iter_ids(self.user_dir)
            ordered = [(item, "builtin") for item in builtin_ids]
            ordered += [
                (item, "user") for item in user_ids if item not in builtin_ids
            ]
            if len(ordered) > MAX_SKILLS:
                raise SkillValidationError(
                    f"Skill 数量超过上限 {MAX_SKILLS}。"
                )
            results: list[SkillSummary] = []
            issues: list[SkillLoadIssue] = []
            for skill_id, source in ordered:
                try:
                    definition = self._load(skill_id)
                except SkillError as exc:
                    issues.append(SkillLoadIssue(
                        id=skill_id, source=source, message=str(exc),
                    ))
                    continue
                results.append(SkillSummary.model_validate(
                    definition.model_dump(exclude={"instructions", "content"})
                ))
            for duplicate in sorted(set(user_ids) & set(builtin_ids)):
                issues.append(SkillLoadIssue(
                    id=duplicate,
                    source="user",
                    message="用户 Skill 与内置 ID 冲突，已使用内置版本。",
                ))
            self._issues = tuple(issues)
            return results

    # 短名称便于 API/调用方使用，同时保留语义明确的 list_skills。
    list = list_skills

    def issues(self) -> tuple[SkillLoadIssue, ...]:
        return self._issues

    def get_skill(self, skill_id: str, *, include_disabled: bool = False) -> SkillDefinition:
        with self._lock:
            definition = self._load(_validate_id(skill_id))
            if not include_disabled and not definition.enabled:
                raise SkillDisabledError(f"Skill {skill_id!r} 当前未启用。")
            return definition

    get = get_skill

    def get_skills(
        self,
        skill_ids: tuple[str, ...] | list[str],
        *,
        include_disabled: bool = False,
    ) -> tuple[SkillDefinition, ...]:
        """在同一把存储锁内冻结一组 Skill；任一失败时不返回部分结果。"""
        with self._lock:
            definitions = tuple(
                self._load(_validate_id(skill_id)) for skill_id in skill_ids
            )
            if not include_disabled:
                disabled = next(
                    (item for item in definitions if not item.enabled), None,
                )
                if disabled is not None:
                    raise SkillDisabledError(
                        f"Skill {disabled.id!r} 当前未启用。"
                    )
            return definitions

    @staticmethod
    def _check_expected_state(
        definition: SkillDefinition,
        *,
        expected_sha256: str | None = None,
        expected_enabled: bool | None = None,
    ) -> None:
        """在任何写入前校验调用方读取到的 Skill 快照。"""
        if (expected_sha256 is not None
                and definition.sha256 != expected_sha256):
            raise SkillConflictError(
                "Skill 内容已被其他操作修改，请刷新后重试。"
            )
        if expected_enabled is not None:
            if not isinstance(expected_enabled, bool):
                raise SkillValidationError("expected_enabled 必须是布尔值。")
            if definition.enabled is not expected_enabled:
                raise SkillConflictError(
                    "Skill 启停状态已被其他操作修改，请刷新后重试。"
                )

    def set_enabled(
        self,
        skill_id: str,
        enabled: bool,
        *,
        expected_sha256: str | None = None,
        expected_enabled: bool | None = None,
    ) -> SkillDefinition:
        if not isinstance(enabled, bool):
            raise SkillValidationError("enabled 必须是布尔值。")
        with self._lock:
            skill_id = _validate_id(skill_id)
            current = self._load(skill_id)  # 不允许为不存在的 ID 留下幽灵状态。
            self._check_expected_state(
                current,
                expected_sha256=expected_sha256,
                expected_enabled=expected_enabled,
            )
            had_previous = skill_id in self._enabled_overrides
            previous = self._enabled_overrides.get(skill_id, False)
            self._enabled_overrides[skill_id] = enabled
            try:
                self._save_state()
            except Exception:
                if not had_previous:
                    self._enabled_overrides.pop(skill_id, None)
                else:
                    self._enabled_overrides[skill_id] = previous
                raise
            return self.get_skill(skill_id, include_disabled=True)

    def _ensure_user_root(self) -> Path:
        if self.user_dir is None:
            raise SkillValidationError("未配置用户 Skill 目录，写操作不可用。")
        self.user_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        return self.user_dir

    def _builtin_exists(self, skill_id: str) -> bool:
        path = self.builtin_dir / skill_id
        return path.is_dir() and not path.is_symlink()

    def create_user_skill(self, skill_id: str, content: str) -> SkillDefinition:
        """创建用户 Skill；先完整验证，再以独占目录写入。"""
        with self._lock:
            skill_id = _validate_id(skill_id)
            if self._builtin_exists(skill_id):
                raise SkillConflictError("不能覆盖同 ID 的内置 Skill。")
            root = self._ensure_user_root()
            target = root / skill_id
            if target.exists() or target.is_symlink():
                raise SkillConflictError(f"用户 Skill {skill_id!r} 已存在。")
            parse_skill_document(skill_id, content, source="user")
            # ID 被外部手工删除后，状态文件里可能残留旧 override。新建必须
            # 以新文档的 enabled 为准，不能意外继承旧版本的启用权限。
            if skill_id in self._enabled_overrides:
                previous = self._enabled_overrides.pop(skill_id)
                try:
                    self._save_state()
                except Exception:
                    self._enabled_overrides[skill_id] = previous
                    raise
            try:
                target.mkdir(mode=0o700)
                self._atomic_write(target / "SKILL.md", content)
            except Exception:
                shutil.rmtree(target, ignore_errors=True)
                raise
            return self.get_skill(skill_id, include_disabled=True)

    def update_user_skill(
        self,
        skill_id: str,
        content: str,
        *,
        expected_sha256: str | None = None,
    ) -> SkillDefinition:
        """更新用户 Skill；内置项即使同名也绝不写入。"""
        with self._lock:
            skill_id = _validate_id(skill_id)
            if self._builtin_exists(skill_id):
                raise SkillConflictError("不能修改内置 Skill。")
            if self.user_dir is None:
                raise SkillNotFoundError(f"用户 Skill {skill_id!r} 不存在。")
            target = self.user_dir / skill_id
            if not target.is_dir() or target.is_symlink():
                raise SkillNotFoundError(f"用户 Skill {skill_id!r} 不存在。")
            current = self.get_skill(skill_id, include_disabled=True)
            self._check_expected_state(
                current, expected_sha256=expected_sha256,
            )
            parse_skill_document(skill_id, content, source="user")
            # 编辑正文与显式启停是两个操作；无论新 frontmatter 带什么默认
            # 值，更新都冻结当前有效状态，避免一次普通保存暗中开关 Skill。
            had_previous = skill_id in self._enabled_overrides
            previous = self._enabled_overrides.get(skill_id, False)
            self._enabled_overrides[skill_id] = current.enabled
            try:
                self._save_state()
            except Exception:
                if had_previous:
                    self._enabled_overrides[skill_id] = previous
                else:
                    self._enabled_overrides.pop(skill_id, None)
                raise
            self._atomic_write(target / "SKILL.md", content)
            return self.get_skill(skill_id, include_disabled=True)

    def delete_user_skill(
        self,
        skill_id: str,
        *,
        expected_sha256: str | None = None,
        expected_enabled: bool | None = None,
    ) -> None:
        """删除整个用户 Skill 包；``rmtree`` 不跟随包内符号链接。"""
        with self._lock:
            skill_id = _validate_id(skill_id)
            if self._builtin_exists(skill_id):
                raise SkillConflictError("不能删除内置 Skill。")
            if self.user_dir is None:
                raise SkillNotFoundError(f"用户 Skill {skill_id!r} 不存在。")
            target = self.user_dir / skill_id
            if not target.is_dir() or target.is_symlink():
                raise SkillNotFoundError(f"用户 Skill {skill_id!r} 不存在。")
            current = self._load(skill_id)
            self._check_expected_state(
                current,
                expected_sha256=expected_sha256,
                expected_enabled=expected_enabled,
            )
            shutil.rmtree(target)
            self._enabled_overrides.pop(skill_id, None)
            self._save_state()

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        try:
            raw = content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise SkillValidationError("SKILL.md 必须是有效 UTF-8 文本。") from exc
        if len(raw) > MAX_SKILL_BYTES:
            raise SkillValidationError(
                f"SKILL.md 超过大小上限 {MAX_SKILL_BYTES} 字节。"
            )
        fd, temporary = tempfile.mkstemp(prefix=".SKILL.md.", dir=path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
