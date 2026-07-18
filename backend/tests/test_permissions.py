import asyncio
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
    normalize_auto_review_root,
    normalize_auto_review_roots,
)
from kylinguard.sessions import SessionStore


def test_自动执行范围必须是规范化的非根绝对路径(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    assert normalize_auto_review_root(str(root / ".." / "workspace")) == str(root.resolve())
    assert normalize_auto_review_roots([str(root), str(root)]) == [str(root.resolve())]
    with pytest.raises(PermissionError, match="绝对路径"):
        normalize_auto_review_root("relative/docs")
    with pytest.raises(PermissionError, match="根目录"):
        normalize_auto_review_root(str(tmp_path.anchor))


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


def test_全局默认ask且权限状态对所有会话生效(store, tmp_path):
    store.create("s1", "记录信息")
    store.create("s2", "另一任务")
    context = store.get_permissions("s1")
    assert context.mode == PermissionMode.ASK
    assert context.full_access_visible is False
    assert context.version == 1

    root = str((tmp_path / "docs").resolve())
    changed = store.set_permission_settings(
        mode=PermissionMode.AUTO_REVIEW,
        auto_review_roots=[root],
        expected_version=1,
        updated_by="admin",
    )
    assert changed.mode == PermissionMode.AUTO_REVIEW
    assert changed.auto_review_roots == [root]
    assert changed.version == 2
    assert changed.updated_by == "admin"
    assert store.get_permission_settings().mode == PermissionMode.AUTO_REVIEW
    assert store.get_permissions("s2").mode == PermissionMode.AUTO_REVIEW
    assert store.get_permissions("s2").auto_review_roots == [root]


def test_完全访问只覆盖指定任务且收回时原子撤销授权(store):
    store.create("s1", "执行")
    store.create("s2", "另一任务")
    grant = store.add_grant(
        "s1",
        scope=PermissionGrantScope.SESSION,
        action_fingerprint="fp",
        capability="files.write",
        resource="/srv/report.md",
        context_version=1,
        granted_by="admin",
        expires_at=time.time() + 60,
    )

    enabled = store.set_session_full_access(
        "s1",
        enabled=True,
        expected_version=1,
        updated_by="admin",
        execution_profile="sha256:test-profile",
    )
    assert enabled.mode == PermissionMode.FULL_ACCESS
    assert enabled.version == 2
    assert store.get_permissions("s2").mode == PermissionMode.ASK
    assert store.get_permissions("s2").version == 1
    assert store.list_grants("s1") == []

    revoked = store.set_session_full_access(
        "s1",
        enabled=False,
        expected_version=2,
        updated_by="admin",
    )
    assert revoked.mode == PermissionMode.ASK
    assert revoked.execution_profile == ""
    assert revoked.version == 1
    assert store.get_permissions("s2").mode == PermissionMode.ASK


def test_自动执行范围可独立于审批模式保存(store, tmp_path):
    root = str((tmp_path / "workspace").resolve())
    store.create("s1", "记录信息")

    changed = store.set_permission_settings(
        mode=PermissionMode.ASK,
        auto_review_roots=[root],
        expected_version=1,
        updated_by="admin",
    )

    assert changed.mode == PermissionMode.ASK
    assert changed.auto_review_roots == [root]


def test_完全访问持续生效并保留自动执行范围(store, tmp_path):
    root = str((tmp_path / "docs").resolve())
    store.create("s1", "记录")
    store.set_permission_settings(
        mode=PermissionMode.ASK, auto_review_roots=[root],
        expected_version=1,
        updated_by="admin",
    )
    store.set_session_full_access(
        "s1", enabled=True, expected_version=2,
        updated_by="admin", execution_profile="sha256:test-profile",
    )
    context = store.get_permissions("s1")
    assert context.mode == PermissionMode.FULL_ACCESS
    assert context.auto_review_roots == [root]
    assert context.version == 3


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
    store.create("s2", "另一个任务")
    grant = store.add_grant(
        "s1", scope=PermissionGrantScope.SESSION,
        action_fingerprint="fp", capability="run_command", resource="",
        context_version=1, granted_by="admin", expires_at=time.time() + 60,
    )
    assert store.list_grants("s1") == [grant]
    other = store.add_grant(
        "s2", scope=PermissionGrantScope.SESSION,
        action_fingerprint="other", capability="run_command", resource="",
        context_version=1, granted_by="admin", expires_at=time.time() + 60,
    )
    assert store.list_grants("s2") == [other]
    changed = store.set_permission_settings(
        mode=PermissionMode.READ_ONLY, auto_review_roots=[],
        expected_version=1, updated_by="admin",
    )
    assert changed.version == 2
    assert store.list_grants("s1") == []
    assert store.list_grants("s2") == []
    with pytest.raises(PermissionVersionConflict):
        store.set_permission_settings(
            mode=PermissionMode.ASK, auto_review_roots=[],
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


def test_权限表是单例全局配置而授权仍按会话保存(store):
    store.create("s1", "任务一")
    store.create("s2", "任务二")
    rows = store._conn.execute(
        "SELECT singleton, mode, version FROM permission_settings"
    ).fetchall()
    assert rows == [(1, "ask", 1)]
    assert store._conn.execute(
        "SELECT COUNT(*) FROM permission_grants"
    ).fetchone()[0] == 0
