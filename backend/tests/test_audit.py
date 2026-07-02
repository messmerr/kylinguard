import sqlite3

import pytest

from kylinguard.audit import AuditError, AuditLog


@pytest.fixture()
def log(tmp_path):
    al = AuditLog(str(tmp_path / "audit.db"))
    yield al
    al.close()


def test_追加事件并形成哈希链(log):
    h1 = log.append("s1", "user_query", {"query": "查看负载"})
    h2 = log.append("s1", "snapshot", {"cpu": "低"})
    events = log.events("s1")
    assert len(events) == 2
    assert events[0]["hash"] == h1
    assert events[1]["prev_hash"] == h1
    assert events[1]["hash"] == h2
    assert events[0]["seq"] == 0 and events[1]["seq"] == 1


def test_会话之间链独立(log):
    log.append("s1", "user_query", {"query": "a"})
    h = log.append("s2", "user_query", {"query": "b"})
    assert log.events("s2")[0]["hash"] == h
    assert log.events("s2")[0]["prev_hash"] == AuditLog.GENESIS


def test_链校验通过与篡改检测(log, tmp_path):
    log.append("s1", "user_query", {"query": "重启 nginx"})
    log.append("s1", "execution", {"cmd": "systemctl restart nginx"})
    assert log.verify_chain("s1") is True
    # 绕过 AuditLog 直改数据库，模拟篡改
    conn = sqlite3.connect(str(tmp_path / "audit.db"))
    conn.execute(
        "UPDATE audit_events SET payload='{\"cmd\": \"rm -rf /\"}' WHERE seq=1"
    )
    conn.commit()
    conn.close()
    assert log.verify_chain("s1") is False


def test_写入失败抛致命错误(log):
    log.close()
    with pytest.raises(AuditError):
        log.append("s1", "user_query", {"query": "x"})
