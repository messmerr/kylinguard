"""控制面 SQLite 文件的最小 OS 权限准备。"""

from __future__ import annotations

import os
from pathlib import Path


def secure_database_path(raw_path: str) -> None:
    """以 0600 创建数据库，并把专用父目录收紧为 0700。

    SQLite 的 WAL/SHM 会继承数据库访问模型；父目录不可被执行账户遍历，
    使 full_access 的独立 OS 用户无法读取或替换审计、认证与权限状态。
    """
    if raw_path == ":memory:":
        return
    path = Path(os.path.abspath(os.path.normpath(
        os.fspath(Path(raw_path).expanduser())
    )))
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent != Path.cwd().resolve(strict=False):
        try:
            parent.chmod(0o700)
        except OSError:
            pass
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


__all__ = ["secure_database_path"]
