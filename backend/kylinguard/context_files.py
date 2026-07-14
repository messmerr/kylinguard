"""工作区文件引用的路径校验与轻量候选搜索。

本模块只处理路径和 stat 元数据，绝不读取文件内容。真正读取仍必须通过
``files.read_file`` 等受权限与审计约束的工具完成。
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Mapping


MAX_CONTEXT_FILES = 8
MAX_CONTEXT_MENTIONS = 12
MAX_CONTEXT_CANDIDATES = 50
MAX_CONTEXT_SCAN_ENTRIES = 4000
MAX_CONTEXT_SCAN_DEPTH = 5

_SKIPPED_NAMES = {
    ".git", ".hg", ".svn", ".ssh", ".gnupg", ".codex",
    ".claude", ".venv", "venv", "node_modules", "__pycache__",
    "dist", "build", ".cache",
}


class ContextFileError(ValueError):
    """文件候选或显式引用不满足工作区边界。"""


class ContextMentionError(ContextFileError):
    """正文 mention 的位置或结构化引用不合法。"""


def normalize_context_mentions(
    message: str,
    mentions: list[dict] | tuple[dict, ...],
    *,
    skill_names: Mapping[str, str],
    context_files: list[dict],
) -> list[dict]:
    """校验 mention 仅引用本轮已选上下文，并由服务端补全显示名称。

    offset 使用 Python 字符串索引语义，即 Unicode code point 起点；mention
    只是呈现与审计元数据，绝不从 message 文本反向推导 Skill 或文件权限。
    """
    if len(mentions) > MAX_CONTEXT_MENTIONS:
        raise ContextMentionError(
            f"一轮最多包含 {MAX_CONTEXT_MENTIONS} 个上下文 mention。"
        )
    file_by_path = {
        str(item.get("relative_path") or ""): item
        for item in context_files if isinstance(item, dict)
    }
    normalized: list[tuple[int, int, int, dict]] = []
    for index, raw in enumerate(mentions):
        if not isinstance(raw, dict):
            raise ContextMentionError("context_mentions 必须是对象数组。")
        offset = raw.get("offset")
        if (not isinstance(offset, int) or isinstance(offset, bool)
                or offset < 0 or offset > len(message)):
            raise ContextMentionError(
                "context_mentions.offset 必须是正文内有效的 Unicode 字符位置。"
            )
        mention_type = raw.get("type")
        if mention_type == "skill":
            skill_id = str(raw.get("skill_id") or "").strip()
            if skill_id not in skill_names:
                raise ContextMentionError(
                    "Skill mention 必须引用本轮 skill_ids 中的已启用 Skill。"
                )
            trusted_name = str(skill_names[skill_id])
            item = {
                "type": "skill",
                "offset": offset,
                "skill_id": skill_id,
                "name": trusted_name,
            }
        elif mention_type == "file":
            path = str(raw.get("path") or "").strip()
            metadata = file_by_path.get(path)
            if metadata is None:
                raise ContextMentionError(
                    "文件 mention 必须引用本轮 context_files 中的规范化路径。"
                )
            trusted_name = str(metadata.get("name") or Path(path).name)
            item = {
                "type": "file",
                "offset": offset,
                "path": path,
                "name": trusted_name,
            }
        else:
            raise ContextMentionError(
                "context_mentions.type 只能是 skill 或 file。"
            )
        token = f"@{trusted_name}"
        if not message.startswith(token, offset):
            raise ContextMentionError(
                "context_mentions.offset 必须精确指向服务端确认的 @名称。"
            )
        normalized.append((offset, offset + len(token), index, item))
    normalized.sort(key=lambda entry: (entry[0], entry[2]))
    previous_end = -1
    for offset, end, _index, _item in normalized:
        if offset < previous_end:
            raise ContextMentionError("context_mentions 之间不能重叠。")
        previous_end = end
    return [item for _offset, _end, _index, item in normalized]


def _workspace_root(value: str | os.PathLike) -> Path:
    try:
        root = Path(value).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ContextFileError("工作目录不存在或无法访问。") from exc
    if not root.is_dir():
        raise ContextFileError("工作目录必须是目录。")
    return root


def _safe_relative_path(raw: str) -> Path:
    value = str(raw or "").strip()
    if not value or len(value) > 4096 or "\x00" in value:
        raise ContextFileError("引用文件路径为空、过长或包含 NUL。")
    relative = Path(value)
    if relative.is_absolute():
        raise ContextFileError("context_files 只接受工作目录内的相对路径。")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ContextFileError("引用文件路径不能包含 . 或 ..。")
    if any(part.startswith(".") or part in _SKIPPED_NAMES
           for part in relative.parts):
        raise ContextFileError("引用文件位于隐藏或敏感目录中。")
    return relative


def validate_context_files(
    workspace_root: str | os.PathLike,
    paths: list[str] | tuple[str, ...],
) -> list[dict]:
    """校验显式文件引用并返回不含内容的稳定路径元数据。"""
    if len(paths) > MAX_CONTEXT_FILES:
        raise ContextFileError(
            f"一次最多引用 {MAX_CONTEXT_FILES} 个文件。"
        )
    root = _workspace_root(workspace_root)
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    file_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        root_fd = os.open(root, directory_flags)
    except OSError as exc:
        raise ContextFileError("工作目录无法安全打开。") from exc
    results: list[dict] = []
    seen: set[str] = set()
    try:
        for raw in paths:
            if not isinstance(raw, str):
                raise ContextFileError("引用文件路径必须是字符串。")
            relative = _safe_relative_path(raw)
            normalized = relative.as_posix()
            if normalized in seen:
                continue
            parent_fd = os.dup(root_fd)
            file_fd = -1
            try:
                for part in relative.parts[:-1]:
                    try:
                        child_fd = os.open(
                            part, directory_flags, dir_fd=parent_fd,
                        )
                    except OSError as exc:
                        raise ContextFileError(
                            f"引用文件路径不能经过符号链接或非目录项：{relative}"
                        ) from exc
                    os.close(parent_fd)
                    parent_fd = child_fd
                try:
                    file_fd = os.open(
                        relative.name, file_flags, dir_fd=parent_fd,
                    )
                except OSError as exc:
                    raise ContextFileError(
                        f"引用文件不存在、无法访问或是符号链接：{relative}"
                    ) from exc
                info = os.fstat(file_fd)
                if not stat.S_ISREG(info.st_mode):
                    raise ContextFileError(f"引用路径不是普通文件：{relative}")
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
                os.close(parent_fd)
            seen.add(normalized)
            results.append({
                "relative_path": normalized,
                "name": relative.name,
                "size": info.st_size,
            })
        return results
    finally:
        os.close(root_fd)


def search_context_files(
    workspace_root: str | os.PathLike,
    query: str = "",
    *,
    limit: int = MAX_CONTEXT_CANDIDATES,
) -> dict:
    """受限遍历工作区，只返回普通文件的相对路径元数据。"""
    root = _workspace_root(workspace_root)
    limit = max(1, min(int(limit), MAX_CONTEXT_CANDIDATES))
    needle = str(query or "").strip().casefold()
    directory_flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        root_fd = os.open(root, directory_flags)
    except OSError as exc:
        raise ContextFileError("工作目录无法安全打开。") from exc
    pending: list[tuple[int, Path, int]] = [(root_fd, Path(), 0)]
    files: list[dict] = []
    scanned = 0
    truncated = False

    try:
        while pending and len(files) < limit:
            directory_fd, relative_dir, depth = pending.pop(0)
            try:
                entries = os.scandir(directory_fd)
            except OSError:
                os.close(directory_fd)
                continue
            try:
                with entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > MAX_CONTEXT_SCAN_ENTRIES:
                            truncated = True
                            break
                        name = entry.name
                        if name.startswith(".") or name in _SKIPPED_NAMES:
                            continue
                        try:
                            if entry.is_symlink():
                                continue
                            relative_path = relative_dir / name
                            if entry.is_dir(follow_symlinks=False):
                                if depth < MAX_CONTEXT_SCAN_DEPTH:
                                    child_fd = os.open(
                                        name, directory_flags,
                                        dir_fd=directory_fd,
                                    )
                                    pending.append((
                                        child_fd, relative_path, depth + 1,
                                    ))
                                continue
                            if not entry.is_file(follow_symlinks=False):
                                continue
                            relative = relative_path.as_posix()
                            if needle and needle not in relative.casefold():
                                continue
                            info = entry.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        files.append({
                            "relative_path": relative,
                            "name": name,
                            "size": info.st_size,
                        })
                        if len(files) >= limit:
                            truncated = True
                            break
            finally:
                os.close(directory_fd)
            if scanned > MAX_CONTEXT_SCAN_ENTRIES:
                break
    finally:
        for directory_fd, _relative, _depth in pending:
            try:
                os.close(directory_fd)
            except OSError:
                pass

    files.sort(key=lambda item: item["relative_path"].casefold())
    return {"files": files, "truncated": truncated, "scanned": scanned}
