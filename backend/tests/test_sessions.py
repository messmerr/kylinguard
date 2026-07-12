import sqlite3

import pytest

from kylinguard.permissions import PermissionError
from kylinguard.sessions import SessionStore


@pytest.fixture()
def store(tmp_path):
    s = SessionStore(str(tmp_path / "kg.db"))
    yield s
    s.close()


def test_创建与列表(store):
    import time
    store.create("s1", "看看负载")
    time.sleep(0.05)  # Windows time.time() 分辨率 ~15ms，确保排序稳定
    store.create("s2", "重启 nginx")
    sessions = store.list()
    assert [s["id"] for s in sessions] == ["s2", "s1"]  # 新的在前
    assert sessions[1]["title"] == "看看负载"


def test_标题超长截断(store):
    store.create("s1", "很长的指令" * 20)
    assert len(store.list()[0]["title"]) <= 30


def test_touch更新排序(store):
    import time
    store.create("s1", "a")
    time.sleep(0.05)
    store.create("s2", "b")
    time.sleep(0.05)
    store.touch("s1")
    assert store.list()[0]["id"] == "s1"


def test_exists(store):
    store.create("s1", "a")
    assert store.exists("s1") is True
    assert store.exists("不存在") is False


def test_重复创建幂等(store):
    store.create("s1", "a")
    store.create("s1", "b")  # 已存在则只 touch，不改标题
    assert len(store.list()) == 1
    assert store.list()[0]["title"] == "a"


def test_草稿严格创建且只由首条消息finalize(store):
    store.create(
        "draft", "新任务", draft=True, strict=True,
        workspace_root="/srv/project",
    )
    summary = store.list()[0]
    assert summary["draft"] is True
    assert summary["title"] == "新任务"
    assert summary["workspace_root"] == "/srv/project"
    assert store.list(include_drafts=False) == []
    assert store.get_workspace_root("draft") == "/srv/project"

    with pytest.raises(PermissionError) as error:
        store.create("draft", "不能覆盖", draft=True, strict=True)
    assert error.value.code == "session_already_exists"

    store.touch("draft", first_message="第一条真实任务")
    summary = store.list()[0]
    assert summary["draft"] is False
    assert summary["title"] == "第一条真实任务"
    assert [item["id"] for item in store.list(include_drafts=False)] == ["draft"]

    store.touch("draft", first_message="第二条消息不能重命名")
    assert store.list()[0]["title"] == "第一条真实任务"


def test_旧会话表自动补draft列且旧记录不是草稿(tmp_path):
    db = tmp_path / "legacy.db"
    connection = sqlite3.connect(db)
    connection.execute(
        "CREATE TABLE sessions ("
        "id TEXT PRIMARY KEY, title TEXT NOT NULL, "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    connection.execute(
        "INSERT INTO sessions VALUES ('legacy', '旧会话', 1, 1)"
    )
    connection.commit()
    connection.close()

    migrated = SessionStore(str(db))
    try:
        summary = migrated.list()[0]
        assert summary["id"] == "legacy"
        assert summary["draft"] is False
        assert summary["workspace_root"] == ""
    finally:
        migrated.close()
