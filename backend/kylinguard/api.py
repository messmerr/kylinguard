"""FastAPI 入口：SSE 对话、人工确认、健康检查、前端静态托管。

启动：uvicorn --factory kylinguard.api:create_app --host 0.0.0.0 --port 8000

M2 待办（设计文档已规划，上线前必须完成）：所有 /api/* 路由加登录鉴权；
confirm 与发起会话绑定同一管理员身份并在审计链中归因。M1 开发阶段仅限
本机/内网联调使用。
"""
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from kylinguard.audit import AuditError, AuditLog
from kylinguard.config import Settings, get_settings
from kylinguard.llm import build_clients
from kylinguard.mcp_client import ToolManager
from kylinguard.pipeline import Confirmations, Pipeline
from kylinguard.planner import Planner
from kylinguard.reviewer import Reviewer
from kylinguard.snapshot import SnapshotCache

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


def create_app(settings: Settings | None = None,
               *, with_tools: bool = True) -> FastAPI:
    settings = settings or get_settings()
    audit = AuditLog(settings.db_path)
    tools = ToolManager()
    confirmations = Confirmations()
    snapshot_cache = SnapshotCache(settings.snapshot_interval)
    planner_llm, reviewer_llm = build_clients(settings)
    pipeline = Pipeline(
        settings=settings, audit=audit, tools=tools,
        planner=Planner(planner_llm, settings.max_json_retries),
        reviewer=Reviewer(reviewer_llm, settings.max_json_retries),
        confirmations=confirmations,
        snapshot_fn=snapshot_cache.get,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if with_tools:
            await tools.start()
            await snapshot_cache.start()
        yield
        if with_tools:
            await snapshot_cache.stop()
            await tools.stop()
        audit.close()

    app = FastAPI(title="麒盾 KylinGuard", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.confirmations = confirmations
    app.state.audit = audit

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/chat")
    async def chat(req: ChatRequest):
        session_id = uuid.uuid4().hex
        queue: asyncio.Queue = asyncio.Queue()

        async def emit(event: dict):
            await queue.put(event)

        async def run():
            try:
                await app.state.pipeline.handle(session_id, req.message, emit)
            except AuditError as e:
                await queue.put({"type": "fatal",
                                 "error": f"审计写入失败，任务已中止：{e}"})
            except Exception as e:  # 不确定收敛到"不执行"，同时不悬挂前端
                await queue.put({"type": "fatal",
                                 "error": f"内部错误，任务已中止：{e}"})
            finally:
                await queue.put(None)

        async def stream():
            task = asyncio.create_task(run())
            try:
                while True:
                    event = await queue.get()
                    if event is None:
                        break
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                yield 'data: {"type": "done"}\n\n'
            finally:
                task.cancel()

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.post("/api/confirm")
    async def confirm(req: ConfirmRequest):
        return {"ok": app.state.confirmations.resolve(req.confirm_id,
                                                      req.approved)}

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True),
                  name="frontend")

    return app
