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
    assert [e["type"] for e in events] == ["user_query", "final_answer", "done"]
    assert events[0]["query"] == "系统怎么样"


async def test_审计失败发fatal事件后收流(app):
    app.state.pipeline = FatalPipeline()
    async with _client(app) as c:
        r = await c.post("/api/chat", json={"message": "x"})
    events = _parse_sse(r.text)
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
