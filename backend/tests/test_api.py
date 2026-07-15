import asyncio
import json
import stat
from pathlib import Path

import httpx
import pytest
from starlette.requests import ClientDisconnect

from kylinguard.api import ChatRequest, create_app
from kylinguard.audit import AuditError
from kylinguard.config import Settings

@pytest.fixture()
def app(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "kg.db"))
    value = create_app(settings, with_tools=False)
    _configure_test_model(value)
    return value


def _configure_test_model(app):
    """接口测试显式建立 GUI 模型配置，不依赖环境变量回退。"""
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


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _request_headers(_client) -> dict:
    return {}


def _parse_sse(text: str) -> list[dict]:
    return [json.loads(line[len("data: "):])
            for line in text.split("\n\n") if line.startswith("data: ")]


class FakePipeline:
    async def handle(self, session_id, user_query, emit):
        await emit({"type": "user_query", "query": user_query})
        await emit({"type": "final_answer", "answer": "一切正常", "aborted": False})


class FatalPipeline:
    async def handle(self, session_id, user_query, emit):
        raise AuditError("磁盘满了")


class UnexpectedPipeline:
    async def handle(self, session_id, user_query, emit):
        raise RuntimeError("secret-provider-body")


class BlockingPipeline:
    def __init__(self):
        self.started = asyncio.Event()
        self.refreshed_sessions = []

    async def handle(self, session_id, user_query, emit):
        self.started.set()
        await asyncio.Event().wait()

    def refresh_session_context(self, session_id):
        self.refreshed_sessions.append(session_id)


class BlockingConfirmPipeline(BlockingPipeline):
    async def handle(self, session_id, user_query, emit):
        await emit({
            "type": "progress", "stage": "reviewing",
            "operation_id": "reviewing:step-1", "step_id": "step-1",
            "tool": "services.restart_service", "state": "completed",
        })
        await emit({
            "type": "confirm_request", "confirm_id": "confirm-1",
            "step_id": "step-1", "step": {}, "decision": {},
        })
        self.started.set()
        await asyncio.Event().wait()


class AuditedEventPipeline(BlockingPipeline):
    def __init__(self, audit):
        super().__init__()
        self.audit = audit

    async def handle(self, session_id, user_query, emit):
        self.audit.append(session_id, "user_query", {"query": user_query})
        self.started.set()
        await emit({"type": "user_query", "query": user_query})
        await asyncio.Event().wait()


class AuditedCompletedPipeline:
    def __init__(self, audit):
        self.audit = audit

    async def handle(self, session_id, user_query, emit):
        self.audit.append(session_id, "user_query", {"query": user_query})
        await emit({"type": "user_query", "query": user_query})
        final = {
            "answer": "已经完成",
            "aborted": False,
            "outcome": "completed",
        }
        self.audit.append(session_id, "final_answer", final)
        await emit({"type": "final_answer", **final})


async def test_health无需鉴权(app):
    async with _client(app) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_登录与登出接口已移除(app):
    async with _client(app) as c:
        login = await c.post("/api/login", json={})
        logout = await c.post("/api/logout")
    assert login.status_code == 405
    assert logout.status_code == 405


async def test_业务端点无需请求凭据(app):
    async with _client(app) as c:
        r1 = await c.post("/api/chat", json={"message": "x"})
        r2 = await c.get("/api/sessions")
        r3 = await c.get("/api/status")
        r4 = await c.post("/api/confirm",
                          json={"confirm_id": "x", "approved": True})
    assert [r.status_code for r in (r1, r2, r3, r4)] == [200] * 4


async def test_chat_SSE流式事件(app):
    app.state.pipeline = FakePipeline()
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.post("/api/chat", json={
            "message": "系统怎么样", "request_id": "turn.test-1",
        },
                         headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert r.headers["x-session-id"] == events[0]["session_id"]
    assert "X-Session-Id" in r.headers["access-control-expose-headers"]
    assert [e["type"] for e in events] == ["session_created", "user_query",
                                           "final_answer", "done"]
    assert events[1]["query"] == "系统怎么样"
    assert events[0]["request_id"] == "turn.test-1"


@pytest.mark.parametrize("request_id", ["包含 空格", "x" * 129])
async def test_chat拒绝不安全request_id(app, request_id):
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.post("/api/chat", headers=h, json={
            "message": "x", "request_id": request_id,
        })
    assert r.status_code == 422


async def test_审计失败发fatal事件后收流(app):
    app.state.pipeline = FatalPipeline()
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.post("/api/chat", json={"message": "x"}, headers=h)
    events = [e for e in _parse_sse(r.text) if e["type"] != "session_created"]
    assert events[0]["type"] == "fatal" and "审计" in events[0]["error"]
    assert events[-1]["type"] == "done"


async def test_未知异常清洗并持久化task_error终态(app):
    app.state.pipeline = UnexpectedPipeline()
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.post("/api/chat", json={
            "message": "x", "request_id": "turn.error-1",
        }, headers=h)
    events = _parse_sse(r.text)
    session_id = events[0]["session_id"]
    stream_events = events[1:]
    assert [e["type"] for e in stream_events] == [
        "task_error", "final_answer", "done",
    ]
    assert stream_events[0]["error"]["code"] == "internal_error"
    assert stream_events[0]["request_id"] == "turn.error-1"
    assert stream_events[1]["aborted"] is True
    assert stream_events[1]["outcome"] == "failed"
    assert "secret-provider-body" not in r.text
    audited = app.state.audit.events(session_id)
    assert [e["event_type"] for e in audited] == [
        "task_error", "final_answer",
    ]


async def test_客户端断流会留下取消终态(app):
    pipeline = BlockingPipeline()
    app.state.pipeline = pipeline
    async with _client(app) as c:
        headers = await _request_headers(c)
        request = asyncio.create_task(
            c.post("/api/chat", json={"message": "等待中"}, headers=headers))
        await asyncio.wait_for(pipeline.started.wait(), timeout=1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    session_id = app.state.sessions.list()[0]["id"]
    audited = app.state.audit.events(session_id)
    assert [event["event_type"] for event in audited] == [
        "task_cancelled", "final_answer",
    ]
    assert audited[-1]["payload"]["outcome"] == "cancelled"
    assert pipeline.refreshed_sessions == [session_id]


async def test_响应头后正文前断流仍保留原始指令并收口(app):
    pipeline = AuditedEventPipeline(app.state.audit)
    app.state.pipeline = pipeline
    chat_endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", "") == "/api/chat"
    )
    response = await chat_endpoint(
        ChatRequest(message="首事件后立即断开"), user="local",
    )

    session_id = app.state.sessions.list()[0]["id"]
    # 端点返回时响应头还未发送；worker 已经启动并将原始指令落入审计。
    assert [event["event_type"] for event in
            app.state.audit.events(session_id)] == ["user_query"]

    sent = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.start":
            raise OSError("client disconnected after headers")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
    }
    with pytest.raises(ClientDisconnect):
        await response(scope, receive, send)

    assert [message["type"] for message in sent] == ["http.response.start"]
    audited = app.state.audit.events(session_id)
    assert [event["event_type"] for event in audited] == [
        "user_query", "task_cancelled", "final_answer",
    ]
    assert audited[0]["payload"]["query"] == "首事件后立即断开"
    assert pipeline.refreshed_sessions == [session_id]


async def test_成功终态后done前断流不会改写为取消(app):
    app.state.pipeline = AuditedCompletedPipeline(app.state.audit)
    chat_endpoint = next(
        route.endpoint for route in app.routes
        if getattr(route, "path", "") == "/api/chat"
    )
    response = await chat_endpoint(
        ChatRequest(message="完成后断开"), user="local",
    )

    assert '"session_created"' in await anext(response.body_iterator)
    assert '"user_query"' in await anext(response.body_iterator)
    final_chunk = await anext(response.body_iterator)
    assert '"final_answer"' in final_chunk
    assert '"completed"' in final_chunk
    await response.body_iterator.aclose()

    session_id = app.state.sessions.list()[0]["id"]
    audited = app.state.audit.events(session_id)
    assert [event["event_type"] for event in audited] == [
        "user_query", "final_answer",
    ]
    assert audited[-1]["payload"]["outcome"] == "completed"


async def test_确认等待中断流会收口确认并记录阶段(app):
    pipeline = BlockingConfirmPipeline()
    app.state.pipeline = pipeline
    async with _client(app) as c:
        headers = await _request_headers(c)
        request = asyncio.create_task(c.post(
            "/api/chat",
            json={"message": "等待确认", "request_id": "turn-cancel-1"},
            headers=headers,
        ))
        await asyncio.wait_for(pipeline.started.wait(), timeout=1)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    session_id = app.state.sessions.list()[0]["id"]
    audited = app.state.audit.events(session_id)
    assert [event["event_type"] for event in audited] == [
        "confirm_result", "task_cancelled", "final_answer",
    ]
    assert audited[0]["payload"]["cancelled"] is True
    cancelled = audited[1]["payload"]
    assert cancelled["stage"] == "reviewing"
    assert cancelled["step_id"] == "step-1"
    assert cancelled["request_id"] == "turn-cancel-1"


async def test_confirm归因到本机操作者(app):
    async with _client(app) as c:
        h = await _request_headers(c)
        cid, fut = app.state.confirmations.create()
        r1 = await c.post("/api/confirm",
                          json={"confirm_id": cid, "approved": True},
                          headers=h)
        r2 = await c.post("/api/confirm",
                          json={"confirm_id": "不存在", "approved": True},
                          headers=h)
    assert r1.json()["ok"] is True
    assert fut.result() == (True, "local")
    assert r2.json()["ok"] is False


async def test_chat自动建会话并可续聊(app):
    app.state.pipeline = FakePipeline()
    async with _client(app) as c:
        h = await _request_headers(c)
        r1 = await c.post("/api/chat", json={"message": "第一条消息"},
                          headers=h)
        events = _parse_sse(r1.text)
        assert events[0]["type"] == "session_created"
        sid = events[0]["session_id"]
        assert sid
        assert r1.headers["x-session-id"] == sid
        r2 = await c.post("/api/chat",
                          json={"message": "第二条", "session_id": sid},
                          headers=h)
        assert r2.headers["x-session-id"] == sid
        types2 = [e["type"] for e in _parse_sse(r2.text)]
        assert "session_created" not in types2
        r3 = await c.get("/api/sessions", headers=h)
        sessions = r3.json()["sessions"]
        assert sessions[0]["id"] == sid
        assert sessions[0]["title"].startswith("第一条消息")


async def test_未知session_id拒绝(app):
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.post("/api/chat",
                         json={"message": "x", "session_id": "不存在的"},
                         headers=h)
    assert r.status_code == 404


async def test_会话事件回放(app):
    app.state.audit.append("sx", "user_query", {"query": "历史问题"})
    app.state.audit.append("sx", "final_answer", {"answer": "历史答案",
                                                  "aborted": False})
    app.state.sessions.create("sx", "历史问题")
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.get("/api/sessions/sx/events", headers=h)
    events = r.json()["events"]
    assert [e["event_type"] for e in events] == ["user_query", "final_answer"]
    assert events[1]["payload"]["answer"] == "历史答案"


async def test_公开会话列表隐藏草稿但草稿仍可回放(app):
    app.state.sessions.create("finalized", "正式任务")
    app.state.sessions.create(
        "draft", "新任务", draft=True, strict=True,
    )
    app.state.audit.append("draft", "permission_changed", {"draft": True})

    async with _client(app) as c:
        h = await _request_headers(c)
        listed = await c.get("/api/sessions", headers=h)
        replayed = await c.get("/api/sessions/draft/events", headers=h)

    assert {item["id"] for item in app.state.sessions.list()} == {
        "draft", "finalized",
    }
    assert [item["id"] for item in listed.json()["sessions"]] == ["finalized"]
    assert [event["event_type"] for event in replayed.json()["events"]] == [
        "permission_changed",
    ]


def test_全新数据目录启动时MCP凭据不会落入数据库旁(tmp_path, monkeypatch):
    state_home = tmp_path / "state-home"
    database_dir = tmp_path / "windows-mounted-workspace" / "data"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    settings = Settings(
        _env_file=None, db_path=str(database_dir / "kylinguard.db"),
    )

    value = create_app(settings, with_tools=False)

    actual = Path(settings.mcp_secrets_dir)
    assert actual == value.state.mcp_config.secrets.directory
    assert actual.is_relative_to(state_home)
    assert actual.parent.name == "mcp-secrets"
    assert stat.S_IMODE(actual.stat().st_mode) == 0o700
    assert not (database_dir / "mcp-secrets").exists()


def test_显式MCP凭据目录保持原路径用于部署持久卷(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state-home"))
    explicit = tmp_path / "persistent-volume" / "mcp-secrets"
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "data" / "kylinguard.db"),
        mcp_secrets_dir=str(explicit),
    )

    value = create_app(settings, with_tools=False)

    assert value.state.mcp_config.secrets.directory == explicit
    assert Path(settings.mcp_secrets_dir) == explicit
    assert stat.S_IMODE(explicit.stat().st_mode) == 0o700
    assert not (tmp_path / "state-home" / "kylinguard" / "mcp-secrets").exists()


async def test_未知会话回放404(app):
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.get("/api/sessions/不存在/events", headers=h)
    assert r.status_code == 404


async def test_status返回快照(app, monkeypatch):
    async def fake_get():
        return {"memory": "充足"}, 5.0

    monkeypatch.setattr(app.state.snapshot_cache, "get", fake_get)
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.get("/api/status", headers=h)
    body = r.json()
    assert body["snapshot"]["memory"] == "充足"
    assert body["collected_ago_seconds"] == 5.0


async def test_审计链校验端点(app):
    app.state.audit.append("sv", "user_query", {"query": "x"})
    app.state.sessions.create("sv", "x")
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.get("/api/sessions/sv/verify", headers=h)
    assert r.json()["ok"] is True


async def test_全局统计端点(app):
    app.state.audit.append("st", "verification",
                           {"decision": {"action": "deny"}})
    app.state.sessions.create("st", "x")
    app.state.sessions.create("draft", "新任务", draft=True, strict=True)
    async with _client(app) as c:
        h = await _request_headers(c)
        r = await c.get("/api/stats", headers=h)
    body = r.json()
    assert body["sessions"] == 1
    assert body["denied"] == 1


async def test_策略CRUD端点(app):
    async with _client(app) as c:
        h = await _request_headers(c)
        r1 = await c.post("/api/policies", headers=h,
                          json={"kind": "blacklist", "pattern": r"\bwipefs\b",
                                "note": "擦除签名"})
        pid = r1.json()["id"]
        r2 = await c.get("/api/policies", headers=h)
        body = r2.json()
        assert body["custom"][0]["id"] == pid
        assert "privilege_escalators" in body["builtin"]
        r3 = await c.post("/api/policies", headers=h,
                          json={"kind": "blacklist", "pattern": "([坏正则"})
        assert r3.status_code == 400
        r4 = await c.delete(f"/api/policies/{pid}", headers=h)
        assert r4.json()["ok"] is True
        r5 = await c.delete("/api/policies/99999", headers=h)
        assert r5.status_code == 404
    events = app.state.audit.events("__policies__")
    assert [event["event_type"] for event in events] == [
        "policy_added", "policy_removed",
    ]
    assert all(event["payload"]["operator"] == "local" for event in events)
    assert app.state.audit.verify_chain("__policies__") is True


async def test_策略审计失败会回滚策略变更(app, monkeypatch):
    original = app.state.audit.append

    def fail_policy_audit(session_id, event_type, payload, **kwargs):
        if event_type == "policy_added":
            raise AuditError("模拟策略审计失败")
        return original(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(app.state.audit, "append", fail_policy_audit)
    async with _client(app) as client:
        headers = await _request_headers(client)
        with pytest.raises(AuditError):
            await client.post("/api/policies", headers=headers, json={
                "kind": "blacklist", "pattern": r"\bdanger-tool\b",
                "note": "必须审计",
            })
    assert app.state.policies.list() == []


async def test_失败服务告警规则归一为布尔条件(app):
    async with _client(app) as c:
        h = await _request_headers(c)
        created = await c.post("/api/alert-rules", headers=h, json={
            "name": "自动启动服务停止",
            "metric": "failed_services",
            "operator": ">=",
            "threshold": 85,
        })
        assert created.status_code == 200
        listed = await c.get("/api/alert-rules", headers=h)
    rule = listed.json()["rules"][0]
    assert rule["operator"] == ">="
    assert rule["threshold"] == 1.0
