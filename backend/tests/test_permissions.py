import asyncio
import sqlite3
import time

import pytest

from kylinguard.models import (
    PermissionDecision,
    PermissionGrantScope,
    PermissionMode,
    PermissionResolution,
)
from kylinguard.permissions import (
    PermissionError,
    PermissionRequests,
    PermissionVersionConflict,
    normalize_trusted_root,
    normalize_trusted_roots,
)
from kylinguard.sessions import SessionStore


def test_可信目录必须是规范化的非根绝对路径(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    assert normalize_trusted_root(str(root / ".." / "workspace")) == str(root.resolve())
    assert normalize_trusted_roots([str(root), str(root)]) == [str(root.resolve())]
    with pytest.raises(PermissionError, match="绝对路径"):
        normalize_trusted_root("relative/docs")
    with pytest.raises(PermissionError, match="根目录"):
        normalize_trusted_root(str(tmp_path.anchor))


async def test_权限请求绑定上下文版本并返回结构化决断():
    requests = PermissionRequests()
    request_id, future = requests.create(
        "s1", "sha256:action", 3, "files.write_file", "/srv/docs/a.md"
    )
    pending = requests.get(request_id)
    assert pending.context_version == 3
    with pytest.raises(PermissionVersionConflict):
        requests.resolve(PermissionResolution(
            request_id=request_id,
            decision=PermissionDecision.ALLOW_ONCE,
            operator="admin",
            context_version=2,
        ))
    assert requests.get(request_id) is not None
    assert requests.resolve(PermissionResolution(
        request_id=request_id,
        decision=PermissionDecision.ALLOW_ONCE,
        operator="admin",
        context_version=3,
    )) is True
    result = await asyncio.wait_for(future, timeout=0.1)
    assert result.decision == PermissionDecision.ALLOW_ONCE
    assert requests.get(request_id) is None


async def test_撤销会话待决请求会以拒绝唤醒等待者():
    requests = PermissionRequests()
    _, first = requests.create("s1", "a", 1, "files.write_file")
    _, second = requests.create("s1", "b", 1, "run_command")
    _, other = requests.create("s2", "c", 1, "run_command")
    assert requests.revoke_session("s1", "admin") == 2
    assert (await first).decision == PermissionDecision.DENY
    assert (await second).operator == "admin"
    assert other.done() is False


@pytest.fixture()
def store(tmp_path):
    value = SessionStore(str(tmp_path / "permissions.db"))
    yield value
    value.close()


def test_会话默认ask且权限状态可持久化(store, tmp_path):
    store.create("s1", "记录信息")
    context = store.get_permissions("s1")
    assert context.mode == PermissionMode.ASK
    assert context.version == 1

    root = str((tmp_path / "docs").resolve())
    changed = store.set_permissions(
        "s1",
        mode=PermissionMode.TRUSTED_WORKSPACE,
        trusted_roots=[root],
        expires_at=time.time() + 60,
        expected_version=1,
        updated_by="admin",
    )
    assert changed.mode == PermissionMode.TRUSTED_WORKSPACE
    assert changed.trusted_roots == [root]
    assert changed.version == 2
    assert changed.updated_by == "admin"
    assert store.list()[0]["permission_mode"] == "trusted_workspace"


def test_过期提权安全回落到ask但保留版本用于并发控制(store, tmp_path):
    root = str((tmp_path / "docs").resolve())
    store.create(
        "s1", "记录", permission_mode=PermissionMode.TRUSTED_WORKSPACE,
        trusted_roots=[root], permission_expires_at=time.time() + 5,
    )
    context = store.get_permissions("s1", now=time.time() + 10)
    assert context.mode == PermissionMode.ASK
    assert context.trusted_roots == []
    assert context.expired is True
    assert context.version == 1

    # 一旦观察到过期，即使系统 wall clock 随后回拨也不能让提权复活。
    rewound = store.get_permissions("s1", now=time.time())
    assert rewound.mode == PermissionMode.ASK
    assert rewound.expired is True


def test_过期授权在时钟回拨后也不会复活(store):
    now = time.time()
    store.create("s1", "执行")
    grant = store.add_grant(
        "s1", scope=PermissionGrantScope.SESSION,
        action_fingerprint="fp", capability="run_command", resource="",
        context_version=1, granted_by="admin", expires_at=now + 5,
    )
    assert store.list_grants("s1", now=now + 10) == []
    assert store.list_grants("s1", now=now) == []
    inactive = store.list_grants("s1", active_only=False)
    assert inactive[0].id == grant.id
    assert inactive[0].expiry_observed_at is not None


def test_权限更新使用乐观版本并使旧授权失效(store):
    store.create("s1", "执行")
    grant = store.add_grant(
        "s1", scope=PermissionGrantScope.SESSION,
        action_fingerprint="fp", capability="run_command", resource="",
        context_version=1, granted_by="admin", expires_at=time.time() + 60,
    )
    assert store.list_grants("s1") == [grant]
    changed = store.set_permissions(
        "s1", mode=PermissionMode.READ_ONLY, trusted_roots=[], expires_at=None,
        expected_version=1, updated_by="admin",
    )
    assert changed.version == 2
    assert store.list_grants("s1") == []
    with pytest.raises(PermissionVersionConflict):
        store.set_permissions(
            "s1", mode=PermissionMode.ASK, trusted_roots=[], expires_at=None,
            expected_version=1, updated_by="admin",
        )


def test_单次授权原子消费而会话授权可重复匹配(store):
    store.create("s1", "执行")
    once = store.add_grant(
        "s1", scope=PermissionGrantScope.ONCE,
        action_fingerprint="once", capability="files.write_file",
        resource="/srv/docs/a.md", context_version=1,
        granted_by="admin", expires_at=time.time() + 60,
    )
    session = store.add_grant(
        "s1", scope=PermissionGrantScope.SESSION,
        action_fingerprint="session", capability="run_command", resource="",
        context_version=1, granted_by="admin", expires_at=time.time() + 60,
    )
    assert store.find_matching_grant(
        "s1", action_fingerprint="once", capability="files.write_file",
        resource="/srv/docs/a.md",
    ) is None
    consumed = store.consume_matching_grant(
        "s1", action_fingerprint="once", capability="files.write_file",
        resource="/srv/docs/a.md", grant_id=once.id,
    )
    assert consumed.id == once.id and consumed.consumed_at is not None
    assert store.consume_matching_grant(
        "s1", action_fingerprint="once", capability="files.write_file",
        resource="/srv/docs/a.md", grant_id=once.id,
    ) is None
    assert store.consume_matching_grant(
        "s1", action_fingerprint="后续同能力的新动作指纹", capability="run_command",
    ) is None
    assert store.consume_matching_grant(
        "s1", action_fingerprint="session", capability="run_command",
    ).id == session.id


def test_中间版本权限表会自动补列(tmp_path):
    db = tmp_path / "legacy-partial.db"
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, title TEXT NOT NULL,
            created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        CREATE TABLE session_permissions (
            session_id TEXT PRIMARY KEY, mode TEXT NOT NULL DEFAULT 'ask'
        );
        CREATE TABLE permission_grants (
            id TEXT PRIMARY KEY, session_id TEXT NOT NULL
        );
    """)
    connection.commit()
    connection.close()

    migrated = SessionStore(str(db))
    try:
        permission_columns = {
            row[1] for row in migrated._conn.execute(
                "PRAGMA table_info(session_permissions)")
        }
        grant_columns = {
            row[1] for row in migrated._conn.execute(
                "PRAGMA table_info(permission_grants)")
        }
        assert {"trusted_roots", "expiry_observed_at", "version"} <= permission_columns
        assert {"resource", "expiry_observed_at", "consumed_at"} <= grant_columns
    finally:
        migrated.close()
