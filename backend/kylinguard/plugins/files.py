"""结构化文本文件工具（Linux/WSL 安全实现）。

所有文件系统操作都从 ``/`` 开始逐级 ``openat``，并在每一级使用
``O_NOFOLLOW`` 固定目录 fd。校验完成后不再重新按完整路径解析，因此本地
并发进程即使把某个路径分量替换成符号链接，也不能把操作引向授权范围外。

写入使用同目录临时文件和 ``renameat2(RENAME_NOREPLACE)`` 发布；移动和
删除会先把已绑定的目录项移入同目录隔离名，再继续操作。权限模式与可信
目录仍由核心门控负责，本插件负责最后一道参数、类型与竞态一致性校验。
"""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError

from kylinguard.plugins._result import reject

mcp = FastMCP("files")

MAX_TEXT_BYTES = 1024 * 1024
MAX_DIRECTORY_ENTRIES = 500
MAX_RECURSIVE_DELETE_ENTRIES = 10_000
_SHA256_LENGTH = 64
_RENAME_NOREPLACE = 1
_HIDDEN_RETRIES = 32

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIR_FLAGS = os.O_RDONLY | _O_CLOEXEC | _O_DIRECTORY | _O_NOFOLLOW
_FILE_FLAGS = os.O_RDONLY | _O_CLOEXEC | _O_NOFOLLOW

_LIBC = ctypes.CDLL(None, use_errno=True) if os.name == "posix" else None
_RENAMEAT2 = getattr(_LIBC, "renameat2", None) if _LIBC is not None else None
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _RENAMEAT2.restype = ctypes.c_int


def _summary(**values: Any) -> str:
    """稳定返回机器可读摘要；修改类调用不得把正文写入结果。"""
    return json.dumps(values, ensure_ascii=False, sort_keys=True)


def _require_secure_platform() -> None:
    if os.name != "posix" or not _O_DIRECTORY or not _O_NOFOLLOW:
        reject("文件工具仅支持具备 openat/O_NOFOLLOW 的 Linux/WSL 执行环境。")
    if _RENAMEAT2 is None:
        reject("当前 Linux C 运行库不支持 renameat2，无法安全执行文件变更。")


def _absolute_path(raw: str, label: str = "path") -> Path:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        reject(f"{label} 参数不合法：路径不能为空或包含 NUL。")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        reject(f"{label} 必须是绝对路径。")
    # 只做词法规范化，不跟随路径中的符号链接。
    return Path(os.path.abspath(os.path.normpath(os.fspath(path))))


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def _same_entry(left: os.stat_result, right: os.stat_result) -> bool:
    return _fingerprint(left) == _fingerprint(right)


def _hidden_name(kind: str) -> str:
    return f".kylinguard-{kind}-{secrets.token_hex(12)}"


def _entry_stat(parent_fd: int, name: str) -> os.stat_result | None:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        reject(f"检查目录项失败：{name}（{exc.strerror or type(exc).__name__}）")


def _open_dir_at(parent_fd: int, name: str, label: str) -> int:
    try:
        descriptor = os.open(name, _DIR_FLAGS, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            reject(f"拒绝访问符号链接或非目录路径：{label}")
        if exc.errno == errno.ENOENT:
            reject(f"目录不存在：{label}")
        reject(f"打开目录失败：{label}（{exc.strerror or type(exc).__name__}）")
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        os.close(descriptor)
        reject(f"目标不是目录：{label}")
    return descriptor


@contextmanager
def _directory_fd(path: Path) -> Iterator[int]:
    """从根目录逐级打开并固定 ``path``，绝不跟随符号链接。"""
    _require_secure_platform()
    try:
        descriptor = os.open(path.anchor or "/", _DIR_FLAGS)
    except OSError as exc:
        reject(f"打开文件系统根目录失败：{exc.strerror or type(exc).__name__}")
    try:
        current_label = path.anchor or "/"
        for part in path.parts[1:]:
            current_label = os.path.join(current_label, part)
            child = _open_dir_at(descriptor, part, current_label)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


@contextmanager
def _parent_fd(path: Path) -> Iterator[tuple[int, str]]:
    if path.parent == path or not path.name:
        reject(f"路径不能是文件系统根目录：{path}")
    with _directory_fd(path.parent) as descriptor:
        yield descriptor, path.name


def _open_bound_entry(parent_fd: int, name: str, display: Path) -> tuple[int, os.stat_result, str]:
    """打开并绑定普通文件/目录，拒绝 stat 与 open 之间发生的替换。"""
    before = _entry_stat(parent_fd, name)
    if before is None:
        reject(f"目标不存在：{display}")
    if stat.S_ISLNK(before.st_mode):
        reject(f"拒绝访问符号链接：{display}")
    if stat.S_ISDIR(before.st_mode):
        flags, kind = _DIR_FLAGS, "directory"
    elif stat.S_ISREG(before.st_mode):
        flags, kind = _FILE_FLAGS, "file"
    else:
        reject(f"目标不是普通文件或目录：{display}")
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            reject(f"目标在检查期间变成了符号链接或其他类型：{display}")
        reject(f"打开目标失败：{display}（{exc.strerror or type(exc).__name__}）")
    bound = os.fstat(descriptor)
    if not _same_entry(before, bound):
        os.close(descriptor)
        reject(f"目标在检查期间发生变化，已取消：{display}")
    return descriptor, bound, kind


def _verify_bound_entry(parent_fd: int, name: str, descriptor: int, display: Path) -> os.stat_result:
    bound = os.fstat(descriptor)
    current = _entry_stat(parent_fd, name)
    if current is None or not _same_entry(current, bound):
        reject(f"目标在操作期间发生变化，已取消：{display}")
    return bound


def _rename_noreplace(
    source_fd: int, source: str, destination_fd: int, destination: str,
) -> None:
    _require_secure_platform()
    ctypes.set_errno(0)
    result = _RENAMEAT2(
        source_fd, os.fsencode(source), destination_fd,
        os.fsencode(destination), _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error = ctypes.get_errno()
    if error == errno.EEXIST:
        raise FileExistsError(error, os.strerror(error), destination)
    if error == errno.ENOENT:
        raise FileNotFoundError(error, os.strerror(error), source)
    raise OSError(error, os.strerror(error), source)


def _quarantine_entry(
    parent_fd: int, name: str, expected: os.stat_result, display: Path,
) -> str:
    """把已绑定目录项原子移到同目录随机名，并验证移动的正是该 inode。"""
    for _ in range(_HIDDEN_RETRIES):
        quarantine = _hidden_name("quarantine")
        try:
            _rename_noreplace(parent_fd, name, parent_fd, quarantine)
        except FileExistsError:
            continue
        except OSError as exc:
            reject(f"隔离目标失败：{display}（{exc.strerror or type(exc).__name__}）")
        moved = _entry_stat(parent_fd, quarantine)
        if moved is not None and _same_entry(moved, expected):
            return quarantine
        # 移动期间源名称被并发替换；只把刚移动的对象放回空闲原名，不删除它。
        try:
            _rename_noreplace(parent_fd, quarantine, parent_fd, name)
        except OSError:
            reject(f"检测到并发替换且无法安全恢复目录项：{display}")
        reject(f"目标在隔离期间发生变化，已取消：{display}")
    reject(f"无法为目标分配安全隔离名称：{display}")


def _restore_quarantine(parent_fd: int, quarantine: str, name: str, display: Path) -> None:
    try:
        _rename_noreplace(parent_fd, quarantine, parent_fd, name)
    except OSError as exc:
        reject(
            f"操作失败且无法安全恢复原目录项：{display}"
            f"（{exc.strerror or type(exc).__name__}）"
        )


def _fsync_directory_fd(descriptor: int) -> None:
    try:
        os.fsync(descriptor)
    except OSError:
        pass


def _validate_sha256(value: str, label: str = "expected_sha256") -> str:
    normalized = value.strip().lower()
    if normalized and (
        len(normalized) != _SHA256_LENGTH
        or any(ch not in "0123456789abcdef" for ch in normalized)
    ):
        reject(f"{label} 必须是 64 位十六进制 SHA-256。")
    return normalized


def _encode_text(content: str) -> bytes:
    try:
        data = content.encode("utf-8")
    except UnicodeEncodeError:
        reject("文本包含无法编码为 UTF-8 的字符。")
    if len(data) > MAX_TEXT_BYTES:
        reject(f"文本过大：{len(data)} 字节，单次上限为 {MAX_TEXT_BYTES} 字节。")
    return data


def _read_fd(descriptor: int, *, display: Path) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        reject(f"目标不是普通文件：{display}")
    if before.st_size > MAX_TEXT_BYTES:
        reject(f"文件过大：{before.st_size} 字节，读取上限为 {MAX_TEXT_BYTES} 字节。")
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = MAX_TEXT_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(128 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    except OSError as exc:
        reject(f"读取文件失败：{display}（{exc.strerror or type(exc).__name__}）")
    data = b"".join(chunks)
    after = os.fstat(descriptor)
    if not _same_entry(before, after):
        reject(f"文件在读取过程中发生变化，已取消：{display}")
    if len(data) > MAX_TEXT_BYTES:
        reject(f"文件读取过程中超过 {MAX_TEXT_BYTES} 字节上限。")
    return data, after


def _hash_fd(descriptor: int, *, display: Path) -> tuple[str, os.stat_result]:
    data, info = _read_fd(descriptor, display=display)
    return hashlib.sha256(data).hexdigest(), info


def _check_expected_hash(path: Path, expected: str, actual: str) -> None:
    if expected and expected != actual:
        reject(f"文件内容已变化，拒绝覆盖：{path}（expected_sha256 不匹配）。")


def _create_temporary(parent_fd: int, mode: int) -> tuple[int, str]:
    for _ in range(_HIDDEN_RETRIES):
        name = _hidden_name("write")
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_CLOEXEC | _O_NOFOLLOW,
                mode,
                dir_fd=parent_fd,
            )
            return descriptor, name
        except FileExistsError:
            continue
        except OSError as exc:
            reject(f"创建同目录临时文件失败：{exc.strerror or type(exc).__name__}")
    reject("无法分配同目录临时文件名。")


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short write")
        view = view[written:]


def _atomic_write_at(
    path: Path,
    parent_fd: int,
    name: str,
    data: bytes,
    *,
    expected_sha256: str,
    create_only: bool,
) -> dict[str, Any]:
    bound_fd = -1
    bound: os.stat_result | None = None
    temporary_fd = -1
    temporary: str | None = None
    quarantine: str | None = None
    published = False
    existed = False
    try:
        existing = _entry_stat(parent_fd, name)
        existed = existing is not None
        if existed:
            bound_fd, bound, kind = _open_bound_entry(parent_fd, name, path)
            if kind != "file":
                reject(f"目标不是可写的普通文件：{path}")
            if create_only:
                reject(f"create_only 写入要求目标不存在：{path}")
            old_sha256, stable = _hash_fd(bound_fd, display=path)
            if not _same_entry(bound, stable):
                reject(f"文件在校验期间发生变化，已取消：{path}")
            bound = stable
            _check_expected_hash(path, expected_sha256, old_sha256)
        elif expected_sha256:
            reject(f"目标不存在，无法匹配 expected_sha256：{path}")

        mode = stat.S_IMODE(bound.st_mode) if bound is not None else 0o600
        temporary_fd, temporary = _create_temporary(parent_fd, mode)
        if hasattr(os, "fchmod"):
            os.fchmod(temporary_fd, mode)
        _write_all(temporary_fd, data)
        os.fsync(temporary_fd)
        os.close(temporary_fd)
        temporary_fd = -1

        if bound is not None:
            stable = _verify_bound_entry(parent_fd, name, bound_fd, path)
            quarantine = _quarantine_entry(parent_fd, name, stable, path)
        try:
            _rename_noreplace(parent_fd, temporary, parent_fd, name)
        except OSError as exc:
            if quarantine is not None:
                _restore_quarantine(parent_fd, quarantine, name, path)
                quarantine = None
            reject(f"发布文件失败：{path}（{exc.strerror or type(exc).__name__}）")
        temporary = None
        published = True
        if quarantine is not None:
            os.unlink(quarantine, dir_fd=parent_fd)
            quarantine = None
        _fsync_directory_fd(parent_fd)
    except ToolError:
        raise
    except OSError as exc:
        reject(f"写入文件失败：{path}（{exc.strerror or type(exc).__name__}）")
    finally:
        if bound_fd >= 0:
            os.close(bound_fd)
        if temporary_fd >= 0:
            os.close(temporary_fd)
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=parent_fd)
            except OSError:
                pass
        # published 后 quarantine 仅可能因旧文件清理失败而残留；不把它
        # 恢复到目标名覆盖已经成功发布的新文件。
        if quarantine is not None and not published:
            try:
                _restore_quarantine(parent_fd, quarantine, name, path)
            except ToolError:
                pass

    return {
        "operation": "created" if not existed else "replaced",
        "path": str(path),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _atomic_write(
    path: Path,
    data: bytes,
    *,
    expected_sha256: str,
    create_only: bool,
) -> dict[str, Any]:
    with _parent_fd(path) as (parent_fd, name):
        return _atomic_write_at(
            path, parent_fd, name, data,
            expected_sha256=expected_sha256,
            create_only=create_only,
        )


@mcp.tool()
def read_file(path: str) -> str:
    """读取一个不超过 1 MiB 的 UTF-8 普通文本文件，并返回内容与哈希。"""
    target = _absolute_path(path)
    with _parent_fd(target) as (parent_fd, name):
        descriptor, _, kind = _open_bound_entry(parent_fd, name, target)
        try:
            if kind != "file":
                reject(f"目标不是普通文件：{target}")
            data, _ = _read_fd(descriptor, display=target)
        finally:
            os.close(descriptor)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        reject(f"文件不是 UTF-8 文本：{target}")
    digest = hashlib.sha256(data).hexdigest()
    return f"path={target}\nbytes={len(data)}\nsha256={digest}\ncontent:\n{text}"


@mcp.tool()
def list_directory(path: str, limit: int = 200) -> str:
    """列出目录项（不跟随符号链接），最多返回 500 项。"""
    if not 1 <= limit <= MAX_DIRECTORY_ENTRIES:
        reject(f"limit 参数不合法：须为 1-{MAX_DIRECTORY_ENTRIES}。")
    target = _absolute_path(path)
    with _directory_fd(target) as descriptor:
        try:
            with os.scandir(descriptor) as iterator:
                children = []
                for _ in range(limit + 1):
                    try:
                        children.append(next(iterator))
                    except StopIteration:
                        break
            truncated = len(children) > limit
            children = sorted(children[:limit], key=lambda item: item.name)
            entries = []
            for child in children:
                child_info = child.stat(follow_symlinks=False)
                kind = (
                    "symlink" if stat.S_ISLNK(child_info.st_mode)
                    else "directory" if stat.S_ISDIR(child_info.st_mode)
                    else "file" if stat.S_ISREG(child_info.st_mode)
                    else "other"
                )
                entries.append({
                    "name": child.name,
                    "type": kind,
                    "bytes": child_info.st_size if kind == "file" else None,
                })
        except OSError as exc:
            reject(f"列出目录失败：{target}（{exc.strerror or type(exc).__name__}）")
    return _summary(
        operation="list_directory", path=str(target),
        entries=entries, truncated=truncated,
    )


@mcp.tool()
def mkdir(path: str, parents: bool = False, exist_ok: bool = False) -> str:
    """创建目录；可显式选择创建父目录或接受目录已存在。"""
    target = _absolute_path(path)
    if target.parent == target:
        reject("拒绝把文件系统根目录作为 mkdir 目标。")
    _require_secure_platform()
    descriptor = os.open(target.anchor or "/", _DIR_FLAGS)
    created_final = False
    try:
        current_label = target.anchor or "/"
        parts = target.parts[1:]
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            current_label = os.path.join(current_label, part)
            try:
                child = os.open(part, _DIR_FLAGS, dir_fd=descriptor)
                existed = True
            except FileNotFoundError:
                if not parents and not final:
                    reject(f"父目录不存在：{Path(current_label).parent}")
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                    existed = False
                    if final:
                        created_final = True
                    _fsync_directory_fd(descriptor)
                except FileExistsError:
                    # 并发创建后仍须用 O_NOFOLLOW 重新绑定；符号链接不会通过。
                    existed = True
                except OSError as exc:
                    reject(
                        f"创建目录失败：{current_label}"
                        f"（{exc.strerror or type(exc).__name__}）"
                    )
                child = _open_dir_at(descriptor, part, current_label)
            except OSError as exc:
                if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                    reject(f"拒绝访问符号链接或非目录路径：{current_label}")
                reject(
                    f"打开目录失败：{current_label}"
                    f"（{exc.strerror or type(exc).__name__}）"
                )
            if final and existed and not exist_ok:
                os.close(child)
                reject(f"目标已存在：{target}")
            os.close(descriptor)
            descriptor = child
    finally:
        os.close(descriptor)
    return _summary(
        operation="created" if created_final else "unchanged",
        path=str(target), type="directory",
    )


@mcp.tool()
def write_file(
    path: str,
    content: str,
    expected_sha256: str = "",
    create_only: bool = False,
) -> str:
    """原子写入 UTF-8 文本；可用旧 SHA-256 防止覆盖并发修改。"""
    target = _absolute_path(path)
    expected = _validate_sha256(expected_sha256)
    return _summary(**_atomic_write(
        target, _encode_text(content),
        expected_sha256=expected, create_only=create_only,
    ))


@mcp.tool()
def replace_text(
    path: str,
    old_text: str,
    new_text: str,
    expected_sha256: str = "",
    replace_all: bool = False,
) -> str:
    """精确替换 UTF-8 文件中的文本，默认要求旧文本只出现一次。"""
    if not old_text:
        reject("old_text 不能为空。")
    target = _absolute_path(path)
    expected = _validate_sha256(expected_sha256)
    with _parent_fd(target) as (parent_fd, name):
        descriptor, _, kind = _open_bound_entry(parent_fd, name, target)
        try:
            if kind != "file":
                reject(f"目标不是普通文件：{target}")
            data, _ = _read_fd(descriptor, display=target)
        finally:
            os.close(descriptor)
        try:
            current_text = data.decode("utf-8")
        except UnicodeDecodeError:
            reject(f"文件不是 UTF-8 文本：{target}")
        current_hash = hashlib.sha256(data).hexdigest()
        _check_expected_hash(target, expected, current_hash)
        matches = current_text.count(old_text)
        if matches == 0:
            reject("未找到 old_text，文件未修改。")
        if matches > 1 and not replace_all:
            reject(f"old_text 共出现 {matches} 次；请提供更精确文本或启用 replace_all。")
        updated = current_text.replace(old_text, new_text, -1 if replace_all else 1)
        # 继续沿用刚才固定的 parent_fd；不能在读取后重新解析完整路径。
        result = _atomic_write_at(
            target, parent_fd, name, _encode_text(updated),
            expected_sha256=current_hash, create_only=False,
        )
    result.update(operation="replace_text", replacements=matches if replace_all else 1)
    return _summary(**result)


def _rollback_move(
    source_parent: int, source_name: str,
    destination_parent: int, destination_name: str,
) -> None:
    try:
        _rename_noreplace(
            destination_parent, destination_name,
            source_parent, source_name,
        )
    except OSError:
        # 绝不为了回滚而覆盖并发创建的目录项。
        pass


@mcp.tool()
def move(
    source: str,
    destination: str,
    expected_sha256: str = "",
    overwrite: bool = False,
    expected_destination_sha256: str = "",
) -> str:
    """在同一文件系统内移动文件或目录；覆盖文件时须绑定目标哈希。"""
    src = _absolute_path(source, "source")
    dst = _absolute_path(destination, "destination")
    if src == dst:
        reject("source 与 destination 不能相同。")
    if src.parent == src:
        reject("拒绝移动文件系统根目录。")
    if dst.is_relative_to(src):
        reject("不能把目录移动到自身内部。")

    expected = _validate_sha256(expected_sha256)
    expected_dst = _validate_sha256(
        expected_destination_sha256, "expected_destination_sha256")

    with _parent_fd(src) as (src_parent, src_name), \
            _parent_fd(dst) as (dst_parent, dst_name):
        src_fd, src_info, src_kind = _open_bound_entry(src_parent, src_name, src)
        dst_fd = -1
        dst_info: os.stat_result | None = None
        quarantine: str | None = None
        moved = False
        try:
            src_hash = ""
            if src_kind == "file":
                src_hash, stable = _hash_fd(src_fd, display=src)
                if not _same_entry(src_info, stable):
                    reject(f"源文件在校验期间发生变化：{src}")
                src_info = stable
            elif expected:
                reject("目录不支持 expected_sha256。")
            _check_expected_hash(src, expected, src_hash)

            destination_info = _entry_stat(dst_parent, dst_name)
            if destination_info is not None:
                if not overwrite:
                    reject(f"目标已存在；未启用 overwrite：{dst}")
                dst_fd, dst_info, dst_kind = _open_bound_entry(
                    dst_parent, dst_name, dst)
                if src_kind != "file" or dst_kind != "file":
                    reject("overwrite 仅支持用普通文件覆盖普通文件。")
                if not expected_dst:
                    reject("覆盖既有目标时必须提供 expected_destination_sha256。")
                dst_hash, stable_dst = _hash_fd(dst_fd, display=dst)
                if not _same_entry(dst_info, stable_dst):
                    reject(f"目标文件在校验期间发生变化：{dst}")
                dst_info = stable_dst
                _check_expected_hash(dst, expected_dst, dst_hash)
            elif expected_dst:
                reject("目标不存在，无法匹配 expected_destination_sha256。")

            stable_src = _verify_bound_entry(src_parent, src_name, src_fd, src)
            if dst_info is not None:
                _verify_bound_entry(dst_parent, dst_name, dst_fd, dst)
                quarantine = _quarantine_entry(dst_parent, dst_name, dst_info, dst)
            try:
                _rename_noreplace(src_parent, src_name, dst_parent, dst_name)
            except OSError as exc:
                if quarantine is not None:
                    _restore_quarantine(dst_parent, quarantine, dst_name, dst)
                    quarantine = None
                reject(f"移动失败：{src} -> {dst}（{exc.strerror or type(exc).__name__}）")
            moved = True
            published = _entry_stat(dst_parent, dst_name)
            if published is None or not _same_entry(published, stable_src):
                _rollback_move(src_parent, src_name, dst_parent, dst_name)
                moved = False
                if quarantine is not None:
                    _restore_quarantine(dst_parent, quarantine, dst_name, dst)
                    quarantine = None
                reject(f"移动期间源目录项被并发替换，已取消：{src}")
            if quarantine is not None:
                os.unlink(quarantine, dir_fd=dst_parent)
                quarantine = None
            _fsync_directory_fd(src_parent)
            if dst_parent != src_parent:
                _fsync_directory_fd(dst_parent)
        finally:
            os.close(src_fd)
            if dst_fd >= 0:
                os.close(dst_fd)
            if quarantine is not None and not moved:
                try:
                    _restore_quarantine(dst_parent, quarantine, dst_name, dst)
                except ToolError:
                    pass

    return _summary(
        operation="move", source=str(src), destination=str(dst),
        type=src_kind, bytes=src_info.st_size if src_kind == "file" else None,
        sha256=src_hash or None,
    )


def _preflight_directory(descriptor: int, count: int = 1) -> int:
    try:
        names = os.listdir(descriptor)
    except OSError as exc:
        reject(f"递归删除预检失败：{exc.strerror or type(exc).__name__}")
    for name in names:
        info = _entry_stat(descriptor, name)
        if info is None:
            reject("递归删除预检期间目录内容发生变化。")
        if stat.S_ISLNK(info.st_mode):
            reject(f"递归删除范围内存在符号链接，拒绝执行：{name}")
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            reject(f"递归删除范围内存在非普通目录项，拒绝执行：{name}")
        count += 1
        if count > MAX_RECURSIVE_DELETE_ENTRIES:
            reject(f"递归删除条目过多：超过 {MAX_RECURSIVE_DELETE_ENTRIES} 项安全上限。")
        if stat.S_ISDIR(info.st_mode):
            child_fd = _open_dir_at(descriptor, name, name)
            try:
                if not _same_entry(info, os.fstat(child_fd)):
                    reject("递归删除预检期间目录被替换。")
                count = _preflight_directory(child_fd, count)
            finally:
                os.close(child_fd)
    return count


def _delete_directory_contents(descriptor: int) -> None:
    """仅相对于已打开目录 fd 删除；每个子项也先绑定并隔离。"""
    names = os.listdir(descriptor)
    for name in names:
        child_fd, child_info, kind = _open_bound_entry(
            descriptor, name, Path(name))
        quarantine: str | None = None
        try:
            stable = _verify_bound_entry(descriptor, name, child_fd, Path(name))
            quarantine = _quarantine_entry(descriptor, name, stable, Path(name))
            if kind == "directory":
                _delete_directory_contents(child_fd)
                os.rmdir(quarantine, dir_fd=descriptor)
            else:
                os.unlink(quarantine, dir_fd=descriptor)
            quarantine = None
        finally:
            os.close(child_fd)
            if quarantine is not None:
                try:
                    _restore_quarantine(descriptor, quarantine, name, Path(name))
                except ToolError:
                    pass
    _fsync_directory_fd(descriptor)


@mcp.tool()
def delete(path: str, expected_sha256: str = "", recursive: bool = False) -> str:
    """删除普通文件或目录；文件可绑定 SHA-256，目录递归须显式开启。"""
    target = _absolute_path(path)
    if target.parent == target:
        reject("拒绝删除文件系统根目录。")
    expected = _validate_sha256(expected_sha256)

    with _parent_fd(target) as (parent_fd, name):
        descriptor, info, kind = _open_bound_entry(parent_fd, name, target)
        quarantine: str | None = None
        count = 1
        digest: str | None = None
        removed = False
        try:
            if kind == "file":
                digest, stable = _hash_fd(descriptor, display=target)
                if not _same_entry(info, stable):
                    reject(f"文件在校验期间发生变化：{target}")
                info = stable
                _check_expected_hash(target, expected, digest)
            else:
                if expected:
                    reject("目录不支持 expected_sha256。")
                if recursive:
                    count = _preflight_directory(descriptor)
                elif os.listdir(descriptor):
                    reject(f"目录非空；如需递归删除请显式设置 recursive=true：{target}")

            stable = _verify_bound_entry(parent_fd, name, descriptor, target)
            quarantine = _quarantine_entry(parent_fd, name, stable, target)
            if kind == "directory":
                if recursive:
                    _delete_directory_contents(descriptor)
                os.rmdir(quarantine, dir_fd=parent_fd)
            else:
                os.unlink(quarantine, dir_fd=parent_fd)
            quarantine = None
            removed = True
            _fsync_directory_fd(parent_fd)
        except OSError as exc:
            reject(f"删除失败：{target}（{exc.strerror or type(exc).__name__}）")
        finally:
            os.close(descriptor)
            if quarantine is not None and not removed:
                try:
                    _restore_quarantine(parent_fd, quarantine, name, target)
                except ToolError:
                    pass

    return _summary(
        operation="delete", path=str(target), type=kind,
        entries=count, sha256=digest,
    )


if __name__ == "__main__":
    mcp.run()
