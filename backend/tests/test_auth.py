import pytest

from kylinguard.auth import AuthStore, TokenManager, hash_password, verify_password


def test_密码哈希与校验():
    h = hash_password("s3cret!")
    assert verify_password("s3cret!", h) is True
    assert verify_password("wrong", h) is False
    assert h != hash_password("s3cret!")  # 随机盐，两次哈希不同


@pytest.fixture()
def store(tmp_path):
    s = AuthStore(str(tmp_path / "kg.db"))
    yield s
    s.close()


def test_ensure_admin与校验(store):
    store.ensure_admin("admin", "pw123")
    assert store.verify("admin", "pw123") is True
    assert store.verify("admin", "bad") is False
    assert store.verify("ghost", "pw123") is False


def test_ensure_admin不覆盖已有密码(store):
    store.ensure_admin("admin", "第一次")
    store.ensure_admin("admin", "第二次")  # 已存在则跳过
    assert store.verify("admin", "第一次") is True


def test_空密码不创建用户(store):
    store.ensure_admin("admin", "")
    assert store.verify("admin", "") is False


def test_token签发校验与吊销():
    tm = TokenManager(ttl_seconds=3600)
    token = tm.issue("admin")
    assert tm.validate(token) == "admin"
    tm.revoke(token)
    assert tm.validate(token) is None
    assert tm.validate("伪造token") is None


def test_token过期():
    tm = TokenManager(ttl_seconds=0)
    token = tm.issue("admin")
    assert tm.validate(token) is None
