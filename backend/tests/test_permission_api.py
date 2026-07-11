import json
import sqlite3
import time
from contextlib import contextmanager

import httpx
import pytest

from kylinguard.api import create_app
from kylinguard.audit import AuditError
from kylinguard.config import Settings
from kylinguard.models import PermissionDecision, PermissionMode

PW = "permission-test-pw"


class FakePipeline:
    async def handle(self, session_id, user_query, emit):
        await emit({"type": "final_answer", "answer": "完成", "aborted": False})


@pytest.fixture()
def app(tmp_path):
    settings = Settings(
        _env_file=None, db_path=str(tmp_path / "permission-api.db"),
        admin_password=PW,
    )
    value = create_app(settings, with_tools=False)
    value.state.pipeline = FakePipeline()
    return value


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(client) -> dict:
    response = await client.post("/api/login", json={
        "username": "admin", "password": PW,
    })
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


def _sse(text: str) -> list[dict]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in text.split("\n\n") if block.startswith("data: ")
    ]


async def test_权限端点需要登录且未知会话404(app):
    async with _client(app) as client:
        unauthorized = await client.get("/api/sessions/s1/permissions")
        headers = await _login(client)
        missing = await client.get(
            "/api/sessions/s1/permissions", headers=headers)
    assert unauthorized.status_code == 401
    assert missing.status_code == 404


async def test_权限批准仅在数据库提交后唤醒流水线(app, monkeypatch):
    app.state.sessions.create("commit-fail", "写文件")
    request_id, future = app.state.permission_requests.create(
        "commit-fail", "fp", 1, "files.write", "/srv/docs/a.md",
    )
    original_transaction = app.state.sessions.transaction

    @contextmanager
    def fail_before_commit():
        with original_transaction() as connection:
            yield connection
            raise sqlite3.OperationalError("模拟 commit 失败")

    monkeypatch.setattr(app.state.sessions, "transaction", fail_before_commit)
    async with _client(app) as client:
        headers = await _login(client)
        with pytest.raises(sqlite3.OperationalError):
            await client.post(
                f"/api/permission-requests/{request_id}/resolve",
                headers=headers,
                json={"decision": "allow_once", "context_version": 1},
            )
    assert future.done() is False
    assert app.state.sessions.list_grants("commit-fail") == []
    assert app.state.audit.events("commit-fail") == []


async def test_新会话首轮可选择非完全访问模式并记录审计(app, tmp_path):
    trusted_root = str((tmp_path / "notes").resolve())
    async with _client(app) as client:
        headers = await _login(client)
        response = await client.post("/api/chat", headers=headers, json={
            "message": "记录信息",
            "permission_mode": "trusted_workspace",
            "trusted_roots": [trusted_root],
            "permission_ttl_seconds": 120,
        })
        session_id = _sse(response.text)[0]["session_id"]
        permission = await client.get(
            f"/api/sessions/{session_id}/permissions", headers=headers)
    assert response.status_code == 200
    assert permission.json()["mode"] == "trusted_workspace"
    assert permission.json()["trusted_roots"] == [trusted_root]
    events = app.state.audit.events(session_id)
    assert events[0]["event_type"] == "permission_changed"
    assert events[0]["payload"]["operator"] == "admin"


async def test_已有会话权限必须通过独立端点且使用版本控制(app, tmp_path):
    app.state.sessions.create("s1", "记录")
    trusted_root = str((tmp_path / "workspace").resolve())
    async with _client(app) as client:
        headers = await _login(client)
        hidden_update = await client.post("/api/chat", headers=headers, json={
            "message": "继续", "session_id": "s1",
            "permission_mode": "read_only",
        })
        changed = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "trusted_workspace", "version": 1,
                "trusted_roots": [trusted_root], "ttl_seconds": 60,
            })
        stale = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "ask", "version": 1,
            })
    assert hidden_update.status_code == 400
    assert hidden_update.json()["detail"]["code"] == "permission_update_requires_endpoint"
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "permission_version_conflict"
    audit = app.state.audit.events("s1")[-1]
    assert audit["event_type"] == "permission_changed"
    assert audit["payload"]["operator"] == "admin"


async def test_完全访问默认关闭并返回明确原因(app):
    app.state.sessions.create("s1", "执行")
    async with _client(app) as client:
        headers = await _login(client)
        status = await client.get(
            "/api/sessions/s1/permissions", headers=headers)
        response = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
                "ttl_seconds": 60, "password": PW,
            })
    assert status.json()["full_access_available"] is False
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "full_access_disabled"


async def test_完全访问要求隔离执行账户和管理员密码复验(tmp_path):
    blocked_settings = Settings(
        _env_file=None, db_path=str(tmp_path / "blocked.db"),
        admin_password=PW, allow_full_access=True,
    )
    blocked_app = create_app(blocked_settings, with_tools=False)
    blocked_app.state.sessions.create("s1", "执行")
    async with _client(blocked_app) as client:
        headers = await _login(client)
        blocked = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
                "ttl_seconds": 60, "password": PW,
            })
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "full_access_requires_isolated_executor"

    allowed_settings = Settings(
        _env_file=None, db_path=str(tmp_path / "allowed.db"),
        admin_password=PW, allow_full_access=True,
        exec_user="nobody",
    )
    allowed_app = create_app(allowed_settings, with_tools=False)
    allowed_app.state.sessions.create("s1", "执行")
    async with _client(allowed_app) as client:
        headers = await _login(client)
        wrong = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
                "ttl_seconds": 60, "password": "wrong",
            })
        enabled = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
                "ttl_seconds": 60, "password": PW,
            })
    assert wrong.status_code == 403
    assert wrong.json()["detail"]["code"] == "full_access_reauthentication_failed"
    assert enabled.status_code == 200
    assert enabled.json()["mode"] == "full_access"
    assert enabled.json()["grants_root"] is False
    assert enabled.json()["execution_identity"] == "nobody"


async def test_权限请求允许一次后生成可消费授权并可查询(app):
    app.state.sessions.create("s1", "写文档")
    request_id, future = app.state.permission_requests.create(
        "s1", "sha256:write-a", 1, "files.write_file", "/srv/docs/a.md"
    )
    async with _client(app) as client:
        headers = await _login(client)
        resolved = await client.post(
            f"/api/permission-requests/{request_id}/resolve",
            headers=headers,
            json={"decision": "allow_once", "context_version": 1},
        )
        listed = await client.get("/api/sessions/s1/grants", headers=headers)
    result = await future
    assert resolved.status_code == 200
    assert result.decision == PermissionDecision.ALLOW_ONCE
    assert listed.json()["grants"][0]["id"] == result.grant_id
    consumed = app.state.sessions.consume_matching_grant(
        "s1", action_fingerprint="sha256:write-a",
        capability="files.write_file", resource="/srv/docs/a.md",
        grant_id=result.grant_id,
    )
    assert consumed.id == result.grant_id
    assert app.state.sessions.list_grants("s1") == []
    audit = app.state.audit.events("s1")[-1]
    assert audit["event_type"] == "permission_resolved"
    assert audit["payload"]["operator"] == "admin"


async def test_高风险权限请求必须由后端复验管理员密码(app):
    app.state.sessions.create("danger", "删除文件")
    request_id, future = app.state.permission_requests.create(
        "danger", "fp-delete", 1, "files.delete", "/srv/docs/a.md",
        requires_reauthentication=True,
    )
    async with _client(app) as client:
        headers = await _login(client)
        missing = await client.post(
            f"/api/permission-requests/{request_id}/resolve",
            headers=headers,
            json={"decision": "allow_once", "context_version": 1},
        )
        overbroad = await client.post(
            f"/api/permission-requests/{request_id}/resolve",
            headers=headers,
            json={"decision": "allow_session", "context_version": 1,
                  "password": PW},
        )
        allowed = await client.post(
            f"/api/permission-requests/{request_id}/resolve",
            headers=headers,
            json={"decision": "allow_once", "context_version": 1,
                  "password": PW},
        )
    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "permission_reauthentication_failed"
    assert overbroad.status_code == 400
    assert overbroad.json()["detail"]["code"] == "high_risk_scope_not_allowed"
    assert allowed.status_code == 200
    assert (await future).decision == PermissionDecision.ALLOW_ONCE


async def test_权限状态与审计原子提交_审计失败不启用完全访问(
    tmp_path, monkeypatch,
):
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "atomic.db"),
        admin_password=PW,
        allow_full_access=True,
        exec_user="nobody",
    )
    app = create_app(settings, with_tools=False)
    app.state.sessions.create("atomic", "执行")
    original = app.state.audit.append

    def fail_permission_change(session_id, event_type, payload, **kwargs):
        if event_type == "permission_changed":
            raise AuditError("模拟审计磁盘故障")
        return original(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_permission_change)
    async with _client(app) as client:
        headers = await _login(client)
        with pytest.raises(AuditError):
            await client.put(
                "/api/sessions/atomic/permissions",
                headers=headers,
                json={"mode": "full_access", "version": 1,
                      "ttl_seconds": 60, "password": PW},
            )
    context = app.state.sessions.get_permissions("atomic")
    assert context.mode == PermissionMode.ASK
    assert context.version == 1


async def test_信任路径决断更新上下文而旧版本请求安全拒绝(app, tmp_path):
    app.state.sessions.create("trust", "写文档")
    trusted_root = str((tmp_path / "trusted").resolve())
    trust_id, trust_future = app.state.permission_requests.create(
        "trust", "fp-trust", 1, "files.write_file", trusted_root,
        suggested_path=trusted_root,
    )
    app.state.sessions.create("stale", "执行")
    stale_id, stale_future = app.state.permission_requests.create(
        "stale", "fp-stale", 1, "run_command",
    )
    app.state.sessions.set_permissions(
        "stale", mode=PermissionMode.READ_ONLY, trusted_roots=[],
        expires_at=None, expected_version=1, updated_by="admin",
    )
    async with _client(app) as client:
        headers = await _login(client)
        trusted = await client.post(
            f"/api/permission-requests/{trust_id}/resolve", headers=headers,
            json={"decision": "trust_path", "context_version": 1,
                  "ttl_seconds": 60},
        )
        stale = await client.post(
            f"/api/permission-requests/{stale_id}/resolve", headers=headers,
            json={"decision": "allow_session", "context_version": 1},
        )
    assert trusted.status_code == 200
    assert trusted.json()["permission"]["mode"] == "trusted_workspace"
    assert trusted.json()["permission"]["trusted_roots"] == [trusted_root]
    assert (await trust_future).decision == PermissionDecision.TRUST_PATH
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "permission_version_conflict"
    assert (await stale_future).decision == PermissionDecision.DENY


async def test_撤销会话授权同时收口待决请求并进入审计(app):
    app.state.sessions.create("s1", "执行")
    grant = app.state.sessions.add_grant(
        "s1", scope="session", action_fingerprint="fp",
        capability="run_command", resource="", context_version=1,
        granted_by="admin", expires_at=time.time() + 60,
    )
    _, pending = app.state.permission_requests.create(
        "s1", "pending", 1, "files.write_file")
    async with _client(app) as client:
        headers = await _login(client)
        response = await client.delete(
            "/api/sessions/s1/grants", headers=headers)
        history = await client.get(
            "/api/sessions/s1/grants?include_inactive=true", headers=headers)
    assert response.json() == {
        "ok": True, "revoked": 1, "cancelled_requests": 1,
    }
    assert (await pending).decision == PermissionDecision.DENY
    assert history.json()["grants"][0]["id"] == grant.id
    assert history.json()["grants"][0]["revoked_at"] is not None
    assert app.state.audit.events("s1")[-1]["event_type"] == "permission_grants_revoked"
