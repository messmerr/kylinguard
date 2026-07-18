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


async def _expose_full_access(client, headers, *, version=1):
    return await client.put(
        "/api/permissions/full-access-visibility",
        headers=headers,
        json={"visible": True, "version": version},
    )


def _sse(text: str) -> list[dict]:
    return [
        json.loads(block.removeprefix("data: "))
        for block in text.split("\n\n") if block.startswith("data: ")
    ]


async def test_全局权限端点无需会话或凭据(app):
    async with _client(app) as client:
        response = await client.get("/api/permissions")
    assert response.status_code == 200
    assert response.json()["mode"] == "ask"
    assert response.json()["full_access_visible"] is False
    assert response.json()["session_id"] == ""


async def test_全局权限端点严格校验合同(app):
    async with _client(app) as client:
        invalid_mode = await client.put("/api/permissions", json={
            "mode": "unknown", "version": 1,
        })
        missing_version = await client.put("/api/permissions", json={
            "mode": "ask",
        })

    assert invalid_mode.status_code == 422
    assert missing_version.status_code == 422


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


async def test_新会话自动继承全局审批模式且不接受会话权限草稿(app, tmp_path):
    auto_root = str((tmp_path / "notes").resolve())
    opened_workspace = tmp_path / "opened-project"
    opened_workspace.mkdir()
    async with _client(app) as client:
        headers = await _request_headers(client)
        changed = await client.put("/api/permissions", headers=headers, json={
            "mode": "auto_review", "version": 1,
            "auto_review_roots": [auto_root],
        })
        response = await client.post("/api/chat", headers=headers, json={
            "message": "记录信息",
            "workspace_root": str(opened_workspace),
        })
        session_id = _sse(response.text)[0]["session_id"]
        rejected_draft = await client.post("/api/chat", headers=headers, json={
            "message": "旧协议", "permission_mode": "read_only",
        })
    assert response.status_code == 200
    assert changed.status_code == 200
    assert rejected_draft.status_code == 422
    permission = app.state.sessions.get_permissions(session_id)
    assert permission.mode == PermissionMode.AUTO_REVIEW
    assert permission.auto_review_roots == [auto_root]
    summary = next(
        item for item in app.state.sessions.list() if item["id"] == session_id
    )
    assert summary["workspace_root"] == str(opened_workspace)
    events = app.state.audit.events("__permissions__")
    assert events[-1]["event_type"] == "permission_changed"
    assert events[-1]["payload"]["operator"] == "local"


async def test_全局权限使用乐观版本并立即作用于已有会话(app, tmp_path):
    app.state.sessions.create("s1", "记录")
    auto_root = str((tmp_path / "workspace").resolve())
    async with _client(app) as client:
        headers = await _request_headers(client)
        changed = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "auto_review", "version": 1,
                "auto_review_roots": [auto_root],
            })
        stale = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "ask", "version": 1,
            })
    assert changed.status_code == 200
    assert changed.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "permission_version_conflict"
    assert app.state.sessions.get_permissions("s1").mode == PermissionMode.AUTO_REVIEW
    audit = app.state.audit.events("__permissions__")[-1]
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
            "/api/permissions", headers=headers)
        response = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
            })
        expose_attempt = await _expose_full_access(client, headers)
    assert status.json()["full_access_available"] is False
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "full_access_disabled"
    assert expose_attempt.status_code == 403
    assert expose_attempt.json()["detail"]["code"] == "full_access_disabled"
    assert "KG_ALLOW_FULL_ACCESS=false" in response.json()["detail"]["message"]


async def test_完全访问默认隐藏且服务端拒绝跳过揭示步骤(app):
    app.state.sessions.create("visibility-pending", "等待授权")
    _, pending = app.state.permission_requests.create(
        "visibility-pending", "fp", 1, "files.write", "/srv/report.md",
    )
    async with _client(app) as client:
        headers = await _request_headers(client)
        hidden_attempt = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
            },
        )
        exposed = await _expose_full_access(client, headers)
        assert pending.done() is False
        enabled = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access", "version": 1,
            },
        )

    assert hidden_attempt.status_code == 403
    assert hidden_attempt.json()["detail"]["code"] == "full_access_hidden"
    assert exposed.status_code == 200
    assert exposed.json()["full_access_visible"] is True
    assert exposed.json()["mode"] == "ask"
    assert exposed.json()["version"] == 1
    assert enabled.status_code == 200
    assert enabled.json()["mode"] == "full_access"
    assert enabled.json()["version"] == 2
    assert pending.done() is True
    events = app.state.audit.events("__permissions__")
    assert [event["event_type"] for event in events[-2:]] == [
        "full_access_visibility_changed", "permission_changed",
    ]


async def test_隐藏入口会立即收回完全访问并使旧版本请求失效(app):
    async with _client(app) as client:
        headers = await _request_headers(client)
        exposed = await _expose_full_access(client, headers)
        enabled = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access", "version": exposed.json()["version"],
            },
        )
        hidden = await client.put(
            "/api/permissions/full-access-visibility",
            headers=headers,
            json={"visible": False, "version": enabled.json()["version"]},
        )
        stale_enable = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access", "version": enabled.json()["version"],
            },
        )

    assert hidden.status_code == 200
    assert hidden.json()["full_access_visible"] is False
    assert hidden.json()["mode"] == "ask"
    assert stale_enable.status_code == 403
    assert stale_enable.json()["detail"]["code"] == "full_access_hidden"


def test_服务端关闭完全访问会在启动时永久收回旧会话(tmp_path):
    db = str(tmp_path / "kill-switch.db")
    store = SessionStore(db)
    store.create(
        "old-full",
        "旧完全访问",
    )
    store.set_permission_settings(
        mode=PermissionMode.FULL_ACCESS, auto_review_roots=[],
        expected_version=1,
        updated_by="admin",
    )
    store.close()

    disabled_app = create_app(Settings(
        _env_file=None,
        db_path=db,

        allow_full_access=False,
    ), with_tools=False)
    context = disabled_app.state.sessions.get_permissions("old-full")
    events = disabled_app.state.audit.events("__permissions__")

    assert context.mode == PermissionMode.ASK
    assert context.version == 3
    assert events[-1]["event_type"] == "permission_changed"
    assert events[-1]["payload"]["reason"] == "full_access_disabled"


def test_后端重启总会收回完全访问并要求重新开启(tmp_path):
    db = str(tmp_path / "profile-change.db")
    store = SessionStore(db)
    store.create(
        "old-profile",
        "旧执行边界",
    )
    store.set_permission_settings(
        mode=PermissionMode.FULL_ACCESS, auto_review_roots=[],
        expected_version=1,
        # 即使执行指纹看似未改变，也不能证明 sudoers/groups/capabilities
        # 没有变化；进程重启后必须重新开启完全访问。
        execution_profile="sha256:any-profile",
        updated_by="admin",
    )
    store.close()

    app = create_app(Settings(
        _env_file=None,
        db_path=db,

        allow_full_access=True,
    ), with_tools=False)
    context = app.state.sessions.get_permissions("old-profile")
    events = app.state.audit.events("__permissions__")

    assert context.mode == PermissionMode.ASK
    assert context.execution_profile == ""
    assert events[-1]["payload"]["reason"] == "service_restarted"


def test_后端重启收回草稿完全访问但保留草稿生命周期(tmp_path):
    db = str(tmp_path / "draft-profile-restart.db")
    store = SessionStore(db)
    store.create(
        "draft-profile",
        "新任务",
        draft=True,
        strict=True,
    )
    store.set_permission_settings(
        mode=PermissionMode.FULL_ACCESS, auto_review_roots=[],
        expected_version=1,
        execution_profile="sha256:any-profile", updated_by="admin",
    )
    store.close()

    restarted = create_app(Settings(
        _env_file=None,
        db_path=db,

        allow_full_access=True,
    ), with_tools=False)
    context = restarted.state.sessions.get_permissions("draft-profile")
    summary = restarted.state.sessions.list()[0]
    events = restarted.state.audit.events("__permissions__")

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
            "/api/permissions", headers=headers)
        create_attempt = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access",
                "version": 1,
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
            "/api/permissions", headers=headers,
        )

    assert response.status_code == 200
    assert response.json()["full_access_available"] is False
    assert "sudo -n" in response.json()["full_access_unavailable_reason"]


async def test_首条消息前开启全局完全访问无需创建草稿会话(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "draft-full.db"),

        workspace_root=str(workspace),
        command_shell="/bin/bash",
    )
    draft_app = create_app(settings, with_tools=False)
    _configure_test_model(draft_app)
    draft_app.state.pipeline = FakePipeline()
    async with _client(draft_app) as client:
        headers = await _request_headers(client)
        exposed = await _expose_full_access(client, headers)
        enabled = await client.put("/api/permissions", headers=headers, json={
            "mode": "full_access",
            "version": exposed.json()["version"],
        })
        listed_before = await client.get("/api/sessions", headers=headers)
        first_turn = await client.post("/api/chat", headers=headers, json={
            "message": "第一条真实任务",
            "workspace_root": str(workspace),
        })
    assert enabled.status_code == 200
    assert enabled.json()["mode"] == "full_access"
    assert enabled.json()["version"] == 2
    assert enabled.json()["execution_profile"]
    assert listed_before.json()["sessions"] == []
    assert first_turn.status_code == 200
    session_id = _sse(first_turn.text)[0]["session_id"]
    context = draft_app.state.sessions.get_permissions(session_id)
    assert context.mode == PermissionMode.FULL_ACCESS
    assert context.version == 2
    assert draft_app.state.sessions.get_workspace_root(session_id) == str(workspace)
    events = draft_app.state.audit.events("__permissions__")
    assert events[-1]["payload"]["source"] == "global_settings"


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
            "/api/permissions", headers=headers)
        exposed = await _expose_full_access(client, headers)
        enabled = await client.put(
            "/api/permissions", headers=headers, json={
                "mode": "full_access", "version": exposed.json()["version"],
            })
    payload = status.json()
    assert payload["full_access_available"] is True
    assert payload["execution_identity"]
    assert payload["execution_identity_source"] == "backend_process"
    assert payload["workspace_root"] == str(workspace)
    assert payload["command_shell"] == "/bin/bash"
    assert payload["command_max_timeout"] == 900
    assert payload["full_access_capabilities"] == [
        "shell", "files", "network", "processes",
    ]
    assert payload["execution_account_separated"] is False
    assert payload["control_plane_isolated"] is False
    assert enabled.status_code == 200
    assert enabled.json()["mode"] == "full_access"
    assert enabled.json()["grants_root"] is False
    assert enabled.json()["execution_identity_source"] == "backend_process"
    assert "expires_at" not in enabled.json()
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

    async with _client(app) as client:
        headers = await _request_headers(client)
        exposed = await _expose_full_access(client, headers)
    assert exposed.status_code == 200

    def fail_permission_change(session_id, event_type, payload, **kwargs):
        if event_type == "permission_changed":
            raise AuditError("模拟审计磁盘故障")
        return original(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_permission_change)
    async with _client(app) as client:
        headers = await _request_headers(client)
        with pytest.raises(AuditError):
            await client.put(
                "/api/permissions",
                headers=headers,
                json={"mode": "full_access", "version": 1},
            )
    context = app.state.sessions.get_permissions("atomic")
    assert context.mode == PermissionMode.ASK
    assert context.full_access_visible is True
    assert context.version == 1


async def test_入口可见性与审计原子提交_审计失败不显示入口(
    app, monkeypatch,
):
    original = app.state.audit.append

    def fail_visibility_change(session_id, event_type, payload, **kwargs):
        if event_type == "full_access_visibility_changed":
            raise AuditError("模拟审计磁盘故障")
        return original(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_visibility_change)
    async with _client(app) as client:
        headers = await _request_headers(client)
        with pytest.raises(AuditError):
            await _expose_full_access(client, headers)

    context = app.state.sessions.get_permission_settings()
    assert context.full_access_visible is False
    assert context.mode == PermissionMode.ASK
    assert context.version == 1


async def test_扩展自动执行范围而旧版本请求安全拒绝(app, tmp_path):
    app.state.sessions.create("review", "写文档")
    app.state.sessions.create("stale", "执行")
    app.state.sessions.set_permission_settings(
        mode=PermissionMode.AUTO_REVIEW, auto_review_roots=[],
        expected_version=1, updated_by="admin",
    )
    auto_root = str((tmp_path / "automatic").resolve())
    review_id, review_future = app.state.permission_requests.create(
        "review", "fp-review", 2, "files.write_file", auto_root,
        suggested_path=auto_root,
    )
    stale_id, stale_future = app.state.permission_requests.create(
        "stale", "fp-stale", 2, "run_command",
    )
    async with _client(app) as client:
        headers = await _request_headers(client)
        authorized = await client.post(
            f"/api/permission-requests/{review_id}/resolve", headers=headers,
            json={"decision": "authorize_path", "context_version": 2},
        )
        stale = await client.post(
            f"/api/permission-requests/{stale_id}/resolve", headers=headers,
            json={"decision": "allow_session", "context_version": 2},
        )
    assert authorized.status_code == 200
    assert authorized.json()["permission"]["mode"] == "auto_review"
    assert authorized.json()["permission"]["auto_review_roots"] == [auto_root]
    assert (await review_future).decision == PermissionDecision.AUTHORIZE_PATH
    assert stale.status_code == 404
    assert stale.json()["detail"]["code"] == "permission_request_not_found"
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
