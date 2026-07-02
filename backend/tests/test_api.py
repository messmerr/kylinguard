import json

import httpx
import pytest

from kylinguard.api import create_app
from kylinguard.audit import AuditError
from kylinguard.config import Settings

PW = "test-pw-123"


@pytest.fixture()
def app(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "kg.db"),
                        admin_password=PW)
    return create_app(settings, with_tools=False)


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _login(c) -> dict:
    r = await c.post("/api/login",
                     json={"username": "admin", "password": PW})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


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


async def test_health无需鉴权(app):
    async with _client(app) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_登录成功与失败(app):
    async with _client(app) as c:
        ok = await c.post("/api/login",
                          json={"username": "admin", "password": PW})
        bad = await c.post("/api/login",
                           json={"username": "admin", "password": "wrong"})
    assert ok.status_code == 200 and ok.json()["token"]
    assert bad.status_code == 401


async def test_业务端点未登录一律401(app):
    async with _client(app) as c:
        r1 = await c.post("/api/chat", json={"message": "x"})
        r2 = await c.get("/api/sessions")
        r3 = await c.get("/api/status")
        r4 = await c.post("/api/confirm",
                          json={"confirm_id": "x", "approved": True})
    assert [r.status_code for r in (r1, r2, r3, r4)] == [401] * 4


async def test_logout后token失效(app):
    async with _client(app) as c:
        h = await _login(c)
        await c.post("/api/logout", headers=h)
        r = await c.get("/api/sessions", headers=h)
    assert r.status_code == 401


async def test_chat_SSE流式事件(app):
    app.state.pipeline = FakePipeline()
    async with _client(app) as c:
        h = await _login(c)
        r = await c.post("/api/chat", json={"message": "系统怎么样"},
                         headers=h)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert [e["type"] for e in events] == ["session_created", "user_query",
                                           "final_answer", "done"]
    assert events[1]["query"] == "系统怎么样"


async def test_审计失败发fatal事件后收流(app):
    app.state.pipeline = FatalPipeline()
    async with _client(app) as c:
        h = await _login(c)
        r = await c.post("/api/chat", json={"message": "x"}, headers=h)
    events = [e for e in _parse_sse(r.text) if e["type"] != "session_created"]
    assert events[0]["type"] == "fatal" and "审计" in events[0]["error"]
    assert events[-1]["type"] == "done"


async def test_confirm归因到登录账号(app):
    async with _client(app) as c:
        h = await _login(c)
        cid, fut = app.state.confirmations.create()
        r1 = await c.post("/api/confirm",
                          json={"confirm_id": cid, "approved": True},
                          headers=h)
        r2 = await c.post("/api/confirm",
                          json={"confirm_id": "不存在", "approved": True},
                          headers=h)
    assert r1.json()["ok"] is True
    assert fut.result() == (True, "admin")  # 决断归因到具体管理员
    assert r2.json()["ok"] is False


async def test_chat自动建会话并可续聊(app):
    app.state.pipeline = FakePipeline()
    async with _client(app) as c:
        h = await _login(c)
        r1 = await c.post("/api/chat", json={"message": "第一条消息"},
                          headers=h)
        events = _parse_sse(r1.text)
        assert events[0]["type"] == "session_created"
        sid = events[0]["session_id"]
        assert sid
        r2 = await c.post("/api/chat",
                          json={"message": "第二条", "session_id": sid},
                          headers=h)
        types2 = [e["type"] for e in _parse_sse(r2.text)]
        assert "session_created" not in types2
        r3 = await c.get("/api/sessions", headers=h)
        sessions = r3.json()["sessions"]
        assert sessions[0]["id"] == sid
        assert sessions[0]["title"].startswith("第一条消息")


async def test_未知session_id拒绝(app):
    async with _client(app) as c:
        h = await _login(c)
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
        h = await _login(c)
        r = await c.get("/api/sessions/sx/events", headers=h)
    events = r.json()["events"]
    assert [e["event_type"] for e in events] == ["user_query", "final_answer"]
    assert events[1]["payload"]["answer"] == "历史答案"


async def test_未知会话回放404(app):
    async with _client(app) as c:
        h = await _login(c)
        r = await c.get("/api/sessions/不存在/events", headers=h)
    assert r.status_code == 404


async def test_status返回快照(app, monkeypatch):
    async def fake_get():
        return {"memory": "充足"}, 5.0

    monkeypatch.setattr(app.state.snapshot_cache, "get", fake_get)
    async with _client(app) as c:
        h = await _login(c)
        r = await c.get("/api/status", headers=h)
    body = r.json()
    assert body["snapshot"]["memory"] == "充足"
    assert body["collected_ago_seconds"] == 5.0


async def test_审计链校验端点(app):
    app.state.audit.append("sv", "user_query", {"query": "x"})
    app.state.sessions.create("sv", "x")
    async with _client(app) as c:
        h = await _login(c)
        r = await c.get("/api/sessions/sv/verify", headers=h)
    assert r.json()["ok"] is True


async def test_全局统计端点(app):
    app.state.audit.append("st", "verification",
                           {"decision": {"action": "deny"}})
    app.state.sessions.create("st", "x")
    async with _client(app) as c:
        h = await _login(c)
        r = await c.get("/api/stats", headers=h)
    body = r.json()
    assert body["sessions"] == 1
    assert body["denied"] == 1


async def test_策略CRUD端点(app):
    async with _client(app) as c:
        h = await _login(c)
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
