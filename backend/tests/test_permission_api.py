import json
import sqlite3
import time
from contextlib import contextmanager
from types import SimpleNamespace

import httpx
import pytest

from kylinguard.api import create_app
from kylinguard.audit import AuditError
from kylinguard.config import Settings
from kylinguard.models import PermissionDecision, PermissionMode
from kylinguard.sessions import SessionStore

class FakePipeline:
    async def handle(self, session_id, user_query, emit):
        await emit({"type": "final_answer", "answer": "完成", "aborted": False})


def _configure_test_model(app):
    """权限测试显式建立 GUI 模型配置，不依赖环境变量回退。"""
    app.state.llm_config.create_provider(
        name="测试模型",
        adapter="openai_compatible",
        base_url="https://llm.example.test/v1",
        models=[{
            "id": "test-model",
            "label": "test-model",
            "enabled": True,
            "supported_efforts": [],
            "supports_temperature": False,
        }],
    )
    return app


@pytest.fixture()
def app(tmp_path):
    settings = Settings(
        _env_file=None, db_path=str(tmp_path / "permission-api.db"),
    )
    value = create_app(settings, with_tools=False)
    _configure_test_model(value)
    value.state.pipeline = FakePipeline()
    return value


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _request_headers(_client) -> dict:
    return {}


def _sse(text: str) -> list[dict]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in text.split("\n\n") if block.startswith("data: ")
    ]


async def test_权限端点无需凭据且未知会话404(app):
    async with _client(app) as client:
        missing = await client.get("/api/sessions/s1/permissions")
    assert missing.status_code == 404


async def test_草稿创建端点无需凭据并严格校验合同(app):
    valid = {
        "session_id": "e" * 32,
        "mode": "full_access",
        "ttl_seconds": 60,
    }
    async with _client(app) as client:
        invalid_id = await client.post(
            "/api/sessions",
            json={**valid, "session_id": "not-a-session-id"},
        )
        invalid_mode = await client.post(
            "/api/sessions",
            json={**valid, "mode": "ask"},
        )

    assert invalid_id.status_code == 422
    assert invalid_mode.status_code == 422
    assert app.state.sessions.exists("e" * 32) is False


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
        headers = await _request_headers(client)
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
    opened_workspace = tmp_path / "opened-project"
    opened_workspace.mkdir()
    async with _client(app) as client:
        headers = await _request_headers(client)
        response = await client.post("/api/chat", headers=headers, json={
            "message": "记录信息",
            "permission_mode": "trusted_workspace",
            "trusted_roots": [trusted_root],
            "permission_ttl_seconds": 120,
            "workspace_root": str(opened_workspace),
        })
        session_id = _sse(response.text)[0]["session_id"]
        permission = await client.get(
            f"/api/sessions/{session_id}/permissions", headers=headers)
    assert response.status_code == 200
    assert permission.json()["mode"] == "trusted_workspace"
    assert permission.json()["trusted_roots"] == [trusted_root]
    assert permission.json()["workspace_root"] == str(opened_workspace)
    summary = next(
        item for item in app.state.sessions.list() if item["id"] == session_id
    )
    assert summary["workspace_root"] == str(opened_workspace)
    events = app.state.audit.events(session_id)
    assert events[0]["event_type"] == "permission_changed"
    assert events[0]["payload"]["operator"] == "local"


async def test_已有会话权限必须通过独立端点且使用版本控制(app, tmp_path):
    app.state.sessions.create("s1", "记录")
    trusted_root = str((tmp_path / "workspace").resolve())
    async with _client(app) as client:
        headers = await _request_headers(client)
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
    assert audit["payload"]["operator"] == "local"


async def test_完全访问可由环境显式关闭并返回明确原因(tmp_path):
    settings = Settings(
        _env_file=None, db_path=str(tmp_path / "disabled.db"),
        allow_full_access=False,
    )
    disabled_app = create_app(settings, with_tools=False)
    disabled_app.state.sessions.create("s1", "执行")
    async with _client(disabled_app) as client:
        headers = await _request_headers(client)
        status = await client.get(
            "/api/sessions/s1/permissions", headers=headers)
        response = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
                "ttl_seconds": 60,
            })
    assert status.json()["full_access_available"] is False
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "full_access_disabled"
    assert "KG_ALLOW_FULL_ACCESS=false" in response.json()["detail"]["message"]


def test_服务端关闭完全访问会在启动时永久收回旧会话(tmp_path):
    db = str(tmp_path / "kill-switch.db")
    store = SessionStore(db)
    store.create(
        "old-full",
        "旧完全访问",
        permission_mode=PermissionMode.FULL_ACCESS,
        permission_expires_at=time.time() + 600,
        updated_by="admin",
    )
    store.close()

    disabled_app = create_app(Settings(
        _env_file=None,
        db_path=db,

        allow_full_access=False,
    ), with_tools=False)
    context = disabled_app.state.sessions.get_permissions("old-full")
    events = disabled_app.state.audit.events("old-full")

    assert context.mode == PermissionMode.ASK
    assert context.version == 2
    assert events[-1]["event_type"] == "permission_changed"
    assert events[-1]["payload"]["reason"] == "full_access_disabled"


def test_后端重启总会收回完全访问并要求重新开启(tmp_path):
    db = str(tmp_path / "profile-change.db")
    store = SessionStore(db)
    store.create(
        "old-profile",
        "旧执行边界",
        permission_mode=PermissionMode.FULL_ACCESS,
        permission_expires_at=time.time() + 600,
        # 即使执行指纹看似未改变，也不能证明 sudoers/groups/capabilities
        # 没有变化；进程重启后必须重新开启完全访问。
        permission_execution_profile="sha256:any-profile",
        updated_by="admin",
    )
    store.close()

    app = create_app(Settings(
        _env_file=None,
        db_path=db,

        allow_full_access=True,
    ), with_tools=False)
    context = app.state.sessions.get_permissions("old-profile")
    events = app.state.audit.events("old-profile")

    assert context.mode == PermissionMode.ASK
    assert context.execution_profile == ""
    assert events[-1]["payload"]["reason"] == "service_restarted"


def test_后端重启收回草稿完全访问但保留草稿生命周期(tmp_path):
    db = str(tmp_path / "draft-profile-restart.db")
    store = SessionStore(db)
    store.create(
        "draft-profile",
        "新任务",
        permission_mode=PermissionMode.FULL_ACCESS,
        permission_expires_at=time.time() + 600,
        permission_execution_profile="sha256:any-profile",
        updated_by="admin",
        draft=True,
        strict=True,
    )
    store.close()

    restarted = create_app(Settings(
        _env_file=None,
        db_path=db,

        allow_full_access=True,
    ), with_tools=False)
    context = restarted.state.sessions.get_permissions("draft-profile")
    summary = restarted.state.sessions.list()[0]
    events = restarted.state.audit.events("draft-profile")

    assert context.mode == PermissionMode.ASK
    assert context.execution_profile == ""
    assert summary["draft"] is True
    assert events[-1]["payload"]["reason"] == "service_restarted"


@pytest.mark.parametrize(("overrides", "message"), [
    ({"workspace_root": "/definitely/missing/kylinguard-workspace"}, "工作目录不可用"),
    ({"command_shell": "/definitely/missing/kylinguard-shell"}, "Shell 不可执行"),
    ({"exec_user": "kylinguard-user-that-does-not-exist"}, "执行账户不存在"),
])
async def test_完全访问不会把无效执行配置报告为可用(
    tmp_path, overrides, message,
):
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "invalid-runtime.db"),

        **overrides,
    )
    invalid_app = create_app(settings, with_tools=False)
    invalid_app.state.sessions.create("s1", "执行")
    async with _client(invalid_app) as client:
        headers = await _request_headers(client)
        status = await client.get(
            "/api/sessions/s1/permissions", headers=headers)
        create_attempt = await client.post(
            "/api/sessions", headers=headers, json={
                "session_id": "d" * 32,
                "mode": "full_access",
                "ttl_seconds": 60,
            },
        )

    assert status.json()["full_access_available"] is False
    assert message in status.json()["full_access_unavailable_reason"]
    assert create_attempt.status_code == 403
    assert invalid_app.state.sessions.exists("d" * 32) is False


async def test_独立执行账户须真实通过非交互sudo与工作目录探测(
    tmp_path, monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "sudo-readiness.db"),

        workspace_root=str(workspace),
        command_shell="/bin/bash",
        exec_user="root",
    )
    readiness_app = create_app(settings, with_tools=False)
    readiness_app.state.sessions.create("s1", "执行")
    monkeypatch.setattr("kylinguard.api.shutil.which", lambda name: "/usr/bin/sudo")
    monkeypatch.setattr(
        "kylinguard.api.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )

    async with _client(readiness_app) as client:
        headers = await _request_headers(client)
        response = await client.get(
            "/api/sessions/s1/permissions", headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["full_access_available"] is False
    assert "sudo -n" in response.json()["full_access_unavailable_reason"]


async def test_首条消息前原子创建完全访问草稿并在首轮finalize(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "draft-full.db"),

        workspace_root=str(workspace),
        command_shell="/bin/bash",
        full_access_max_ttl=600,
    )
    draft_app = create_app(settings, with_tools=False)
    _configure_test_model(draft_app)
    draft_app.state.pipeline = FakePipeline()
    session_id = "a" * 32

    async with _client(draft_app) as client:
        headers = await _request_headers(client)
        created = await client.post("/api/sessions", headers=headers, json={
            "session_id": session_id,
            "mode": "full_access",
            "ttl_seconds": 120,
            "workspace_root": str(workspace),
        })
        listed_before = await client.get("/api/sessions", headers=headers)
        first_turn = await client.post("/api/chat", headers=headers, json={
            "message": "第一条真实任务",
            "session_id": session_id,
        })
        listed_after = await client.get("/api/sessions", headers=headers)

    assert created.status_code == 201
    assert created.json()["session_id"] == session_id
    assert created.json()["draft"] is True
    permission = created.json()["permission"]
    assert permission["mode"] == "full_access"
    assert permission["version"] == 1
    assert permission["execution_profile"]
    assert permission["expires_at"] is not None
    assert permission["workspace_root"] == str(workspace)

    before = listed_before.json()
    assert before["sessions"][0]["draft"] is True
    assert before["sessions"][0]["title"] == "新任务"
    assert before["sessions"][0]["workspace_root"] == str(workspace)
    assert before["permission_capabilities"]["full_access_available"] is True
    assert before["permission_capabilities"]["execution_identity"]

    assert first_turn.status_code == 200
    assert "session_created" not in [event["type"] for event in _sse(first_turn.text)]
    after = listed_after.json()["sessions"][0]
    assert after["draft"] is False
    assert after["title"] == "第一条真实任务"
    events = draft_app.state.audit.events(session_id)
    assert events[0]["event_type"] == "permission_changed"
    assert events[0]["payload"]["source"] == "pre_message"
    assert events[0]["payload"]["draft"] is True


async def test_草稿创建先校验且冲突不会覆盖已有会话(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "draft-validation.db"),

        workspace_root=str(workspace),
        command_shell="/bin/bash",
        full_access_max_ttl=60,
    )
    draft_app = create_app(settings, with_tools=False)
    _configure_test_model(draft_app)
    session_id = "b" * 32

    async with _client(draft_app) as client:
        headers = await _request_headers(client)
        excessive_ttl = await client.post(
            "/api/sessions", headers=headers, json={
                "session_id": session_id,
                "mode": "full_access",
                "ttl_seconds": 61,
            },
        )
        exists_after_excessive_ttl = draft_app.state.sessions.exists(session_id)
        invalid_workspace = await client.post(
            "/api/sessions", headers=headers, json={
                "session_id": session_id,
                "mode": "full_access",
                "ttl_seconds": 60,
                "workspace_root": str(tmp_path / "missing-workspace"),
            },
        )
        exists_after_invalid_workspace = draft_app.state.sessions.exists(session_id)
        created = await client.post(
            "/api/sessions", headers=headers, json={
                "session_id": session_id,
                "mode": "full_access",
                "ttl_seconds": 60,
            },
        )
        conflict = await client.post(
            "/api/sessions", headers=headers, json={
                "session_id": session_id,
                "mode": "full_access",
                "ttl_seconds": 60,
            },
        )

    assert excessive_ttl.status_code == 400
    assert exists_after_excessive_ttl is False
    assert invalid_workspace.status_code == 400
    assert invalid_workspace.json()["detail"]["code"] == "workspace_root_unavailable"
    assert exists_after_invalid_workspace is False
    assert draft_app.state.sessions.exists(session_id) is True
    assert created.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "session_already_exists"
    summaries = draft_app.state.sessions.list()
    assert len(summaries) == 1
    assert summaries[0]["draft"] is True
    assert len(draft_app.state.audit.events(session_id)) == 1


async def test_草稿创建审计失败会整体回滚(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    draft_app = create_app(Settings(
        _env_file=None,
        db_path=str(tmp_path / "draft-rollback.db"),

        workspace_root=str(workspace),
        command_shell="/bin/bash",
    ), with_tools=False)
    _configure_test_model(draft_app)
    session_id = "c" * 32

    def fail_audit(*_args, **_kwargs):
        raise AuditError("模拟草稿审计失败")

    monkeypatch.setattr(draft_app.state.audit, "append", fail_audit)
    async with _client(draft_app) as client:
        headers = await _request_headers(client)
        with pytest.raises(AuditError, match="草稿审计失败"):
            await client.post("/api/sessions", headers=headers, json={
                "session_id": session_id,
                "mode": "full_access",
                "ttl_seconds": 60,
            })

    assert draft_app.state.sessions.exists(session_id) is False


async def test_完全访问无需账户但仍受TTL限制(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = Settings(
        _env_file=None, db_path=str(tmp_path / "full-access.db"),
        workspace_root=str(workspace),
        command_shell="/bin/bash", command_max_timeout=900,
    )
    full_app = create_app(settings, with_tools=False)
    full_app.state.sessions.create("s1", "执行")
    async with _client(full_app) as client:
        headers = await _request_headers(client)
        status = await client.get(
            "/api/sessions/s1/permissions", headers=headers)
        enabled = await client.put(
            "/api/sessions/s1/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
                "ttl_seconds": 60,
            })
    payload = status.json()
    assert payload["full_access_available"] is True
    assert payload["execution_identity"]
    assert payload["execution_identity_source"] == "backend_process"
    assert payload["workspace_root"] == str(workspace)
    assert payload["command_shell"] == "/bin/bash"
    assert payload["command_max_timeout"] == 900
    assert payload["permission_default_ttl"] == 1800
    assert payload["full_access_capabilities"] == [
        "shell", "files", "network", "processes",
    ]
    assert payload["execution_account_separated"] is False
    assert payload["control_plane_isolated"] is False
    assert enabled.status_code == 200
    assert enabled.json()["mode"] == "full_access"
    assert enabled.json()["grants_root"] is False
    assert enabled.json()["execution_identity_source"] == "backend_process"
    assert enabled.json()["expires_at"] is not None
    assert full_app.state.sessions.get_permissions("s1").execution_profile


async def test_权限请求允许一次后生成可消费授权并可查询(app):
    app.state.sessions.create("s1", "写文档")
    request_id, future = app.state.permission_requests.create(
        "s1", "sha256:write-a", 1, "files.write_file", "/srv/docs/a.md"
    )
    async with _client(app) as client:
        headers = await _request_headers(client)
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
    assert audit["payload"]["operator"] == "local"


async def test_高风险权限请求只能单次授权(app):
    app.state.sessions.create("danger", "删除文件")
    request_id, future = app.state.permission_requests.create(
        "danger", "fp-delete", 1, "files.delete", "/srv/docs/a.md",
        single_action_only=True,
    )
    async with _client(app) as client:
        headers = await _request_headers(client)
        overbroad = await client.post(
            f"/api/permission-requests/{request_id}/resolve",
            headers=headers,
            json={"decision": "allow_session", "context_version": 1},
        )
        allowed = await client.post(
            f"/api/permission-requests/{request_id}/resolve",
            headers=headers,
            json={"decision": "allow_once", "context_version": 1},
        )
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

        allow_full_access=True,
        exec_user="",
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
        headers = await _request_headers(client)
        with pytest.raises(AuditError):
            await client.put(
                "/api/sessions/atomic/permissions",
                headers=headers,
                json={"mode": "full_access", "version": 1,
                      "ttl_seconds": 60},
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
        headers = await _request_headers(client)
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
        headers = await _request_headers(client)
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
