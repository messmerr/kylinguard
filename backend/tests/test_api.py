import json

import httpx
import pytest

from kylinguard.api import create_app
from kylinguard.audit import AuditError
from kylinguard.config import Settings


@pytest.fixture()
def app(tmp_path):
    settings = Settings(_env_file=None, db_path=str(tmp_path / "kg.db"))
    return create_app(settings, with_tools=False)


def _client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test")


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


async def test_health(app):
    async with _client(app) as c:
        r = await c.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


async def test_chat_SSE流式事件(app):
    app.state.pipeline = FakePipeline()
    async with _client(app) as c:
        r = await c.post("/api/chat", json={"message": "系统怎么样"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(r.text)
    assert [e["type"] for e in events] == ["session_created", "user_query",
                                           "final_answer", "done"]
    assert events[1]["query"] == "系统怎么样"


async def test_审计失败发fatal事件后收流(app):
    app.state.pipeline = FatalPipeline()
    async with _client(app) as c:
        r = await c.post("/api/chat", json={"message": "x"})
    events = [e for e in _parse_sse(r.text) if e["type"] != "session_created"]
    assert events[0]["type"] == "fatal" and "审计" in events[0]["error"]
    assert events[-1]["type"] == "done"


async def test_confirm接口(app):
    cid, fut = app.state.confirmations.create()
    async with _client(app) as c:
        r1 = await c.post("/api/confirm",
                          json={"confirm_id": cid, "approved": True})
        r2 = await c.post("/api/confirm",
                          json={"confirm_id": "不存在", "approved": True})
    assert r1.json()["ok"] is True and fut.result() is True
    assert r2.json()["ok"] is False


async def test_chat自动建会话并可续聊(app):
    app.state.pipeline = FakePipeline()
    async with _client(app) as c:
        r1 = await c.post("/api/chat", json={"message": "第一条消息"})
        events = _parse_sse(r1.text)
        assert events[0]["type"] == "session_created"
        sid = events[0]["session_id"]
        assert sid
        # 带 session_id 续聊：不再发 session_created
        r2 = await c.post("/api/chat",
                          json={"message": "第二条", "session_id": sid})
        types2 = [e["type"] for e in _parse_sse(r2.text)]
        assert "session_created" not in types2
        # 会话列表
        r3 = await c.get("/api/sessions")
        sessions = r3.json()["sessions"]
        assert sessions[0]["id"] == sid
        assert sessions[0]["title"].startswith("第一条消息")


async def test_未知session_id拒绝(app):
    async with _client(app) as c:
        r = await c.post("/api/chat",
                         json={"message": "x", "session_id": "不存在的"})
    assert r.status_code == 404


async def test_会话事件回放(app):
    app.state.audit.append("sx", "user_query", {"query": "历史问题"})
    app.state.audit.append("sx", "final_answer", {"answer": "历史答案",
                                                  "aborted": False})
    app.state.sessions.create("sx", "历史问题")
    async with _client(app) as c:
        r = await c.get("/api/sessions/sx/events")
    events = r.json()["events"]
    assert [e["event_type"] for e in events] == ["user_query", "final_answer"]
    assert events[1]["payload"]["answer"] == "历史答案"


async def test_未知会话回放404(app):
    async with _client(app) as c:
        r = await c.get("/api/sessions/不存在/events")
    assert r.status_code == 404


async def test_status返回快照(app, monkeypatch):
    async def fake_get():
        return {"memory": "充足"}, 5.0

    monkeypatch.setattr(app.state.snapshot_cache, "get", fake_get)
    async with _client(app) as c:
        r = await c.get("/api/status")
    body = r.json()
    assert body["snapshot"]["memory"] == "充足"
    assert body["collected_ago_seconds"] == 5.0
