import stat

import pytest

from kylinguard.storage_security import secure_database_path


def test_控制面数据库与专用目录使用最小权限(tmp_path):
    db = tmp_path / "state" / "control.db"
    secure_database_path(str(db))
    assert stat.S_IMODE(db.stat().st_mode) == 0o600
    assert stat.S_IMODE(db.parent.stat().st_mode) == 0o700


def test_控制面数据库路径不能是符号链接(tmp_path):
    target = tmp_path / "real.db"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "linked.db"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("当前文件系统不支持符号链接")
    with pytest.raises(OSError):
        secure_database_path(str(link))
