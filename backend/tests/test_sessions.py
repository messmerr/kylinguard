import pytest

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
