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

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from kylinguard.audit import AuditError, AuditLog
from kylinguard.auth import AuthStore, TokenManager
from kylinguard.config import Settings, get_settings
from kylinguard.llm import build_clients
from kylinguard.mcp_client import ToolManager
from kylinguard.pipeline import Confirmations, Pipeline
from kylinguard.planner import Planner
from kylinguard.policy import KINDS, PolicyStore
from kylinguard.reviewer import Reviewer
from kylinguard.rules import builtin_rules
from kylinguard.alert_rules import AlertRuleStore
from kylinguard.alert_pusher import push_channel
from kylinguard.sessions import SessionStore
from kylinguard.snapshot import SnapshotCache

_FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class ChatRequest(BaseModel):
    message: str
    session_id: str = ""  # 空 = 新建会话（SSE 首事件返回 session_created）


class ConfirmRequest(BaseModel):
    confirm_id: str
    approved: bool


class LoginRequest(BaseModel):
    username: str
    password: str


class PolicyRequest(BaseModel):
    kind: str
    pattern: str
    note: str = ""


class AlertRuleRequest(BaseModel):
    name: str
    metric: str
    operator: str = ">="
    threshold: float = 90.0
    severity: str = "warning"
    silence_minutes: int = 10
    channel_ids: list[int] = Field(default_factory=list)
    enabled: bool = True

    @model_validator(mode="after")
    def normalize_boolean_metric(self):
        if self.metric == "failed_services":
            self.operator = ">="
            self.threshold = 1.0
        return self


class AlertChannelRequest(BaseModel):
    name: str
    type: str
    config: dict
    enabled: bool = True


def create_app(settings: Settings | None = None,
               *, with_tools: bool = True) -> FastAPI:
    settings = settings or get_settings()
    audit = AuditLog(settings.db_path)
    sessions = SessionStore(settings.db_path)
    auth_store = AuthStore(settings.db_path)
    auth_store.ensure_admin(settings.admin_user, settings.admin_password)
    tokens = TokenManager(settings.token_ttl)
    policies = PolicyStore(settings.db_path)
    tools = ToolManager()
    confirmations = Confirmations()
    snapshot_cache = SnapshotCache(settings.snapshot_interval)
    alert_rule_store = AlertRuleStore(settings.db_path)
    snapshot_cache.set_rule_store(alert_rule_store)
    planner_llm, reviewer_llm = build_clients(settings)
    pipeline = Pipeline(
        settings=settings, audit=audit, tools=tools,
        planner=Planner(planner_llm, settings.max_json_retries),
        reviewer=Reviewer(reviewer_llm, settings.max_json_retries),
        confirmations=confirmations,
        snapshot_fn=snapshot_cache.get,
        policy_store=policies,
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
        sessions.close()
        auth_store.close()
        policies.close()
        alert_rule_store.close()
        audit.close()

    app = FastAPI(title="麒盾 KylinGuard", lifespan=lifespan)
    app.state.pipeline = pipeline
    app.state.confirmations = confirmations
    app.state.audit = audit
    app.state.sessions = sessions
    app.state.snapshot_cache = snapshot_cache
    app.state.alert_rule_store = alert_rule_store
    app.state.auth = auth_store
    app.state.tokens = tokens
    app.state.policies = policies

    async def require_auth(authorization: str = Header("")) -> str:
        """所有业务端点的鉴权依赖：返回当前管理员用户名。"""
        token = authorization.removeprefix("Bearer ").strip()
        username = app.state.tokens.validate(token)
        if not username:
            raise HTTPException(401, "未登录或登录已过期")
        return username

    @app.get("/api/health")
    async def health():
        return {"status": "ok"}

    @app.post("/api/login")
    async def login(req: LoginRequest):
        if not app.state.auth.verify(req.username, req.password):
            raise HTTPException(401, "用户名或密码错误")
        return {"token": app.state.tokens.issue(req.username),
                "username": req.username}

    @app.post("/api/logout")
    async def logout(authorization: str = Header("")):
        app.state.tokens.revoke(
            authorization.removeprefix("Bearer ").strip())
        return {"ok": True}

    @app.post("/api/chat")
    async def chat(req: ChatRequest, _user: str = Depends(require_auth)):
        created = not req.session_id
        if created:
            session_id = uuid.uuid4().hex
            app.state.sessions.create(session_id, req.message)
        else:
            session_id = req.session_id
            if not app.state.sessions.exists(session_id):
                raise HTTPException(404, "会话不存在")
            app.state.sessions.touch(session_id)
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
            if created:
                yield ("data: " + json.dumps(
                    {"type": "session_created", "session_id": session_id},
                    ensure_ascii=False) + "\n\n")
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
    async def confirm(req: ConfirmRequest,
                      user: str = Depends(require_auth)):
        # operator 随 confirm_result 写入审计链：确认决断归因到管理员账号
        return {"ok": app.state.confirmations.resolve(
            req.confirm_id, req.approved, operator=user)}

    @app.get("/api/sessions")
    async def list_sessions(_user: str = Depends(require_auth)):
        return {"sessions": app.state.sessions.list()}

    @app.get("/api/sessions/{session_id}/events")
    async def session_events(session_id: str,
                             _user: str = Depends(require_auth)):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        return {"events": app.state.audit.events(session_id)}

    @app.get("/api/sessions/{session_id}/verify")
    async def session_verify(session_id: str,
                             _user: str = Depends(require_auth)):
        if not app.state.sessions.exists(session_id):
            raise HTTPException(404, "会话不存在")
        return {"ok": app.state.audit.verify_chain(session_id)}

    @app.get("/api/stats")
    async def stats(_user: str = Depends(require_auth)):
        return {"sessions": len(app.state.sessions.list()),
                **app.state.audit.stats()}

    @app.get("/api/status")
    async def status(_user: str = Depends(require_auth)):
        import json as _j
        snapshot, age = await app.state.snapshot_cache.get()
        body = _j.dumps({"snapshot": snapshot,
                          "collected_ago_seconds": round(age, 1)},
                        ensure_ascii=False)
        from fastapi.responses import Response as _R
        return _R(content=body.encode("utf-8"), media_type="application/json")

    @app.get("/api/alerts")
    async def list_alerts(_user: str = Depends(require_auth)):
        return {"alerts": app.state.snapshot_cache.alert_store.active()}

    @app.post("/api/alerts/{alert_id}/ack")
    async def ack_alert(alert_id: str, _user: str = Depends(require_auth)):
        if not app.state.snapshot_cache.alert_store.ack(alert_id):
            raise HTTPException(404, "告警不存在")
        return {"ok": True}

    # ---- 告警规则 ----

    @app.get("/api/alert-rules")
    async def list_alert_rules(_user: str = Depends(require_auth)):
        rules = app.state.alert_rule_store.list_rules()
        return {"rules": [vars(r) for r in rules]}

    @app.post("/api/alert-rules")
    async def create_alert_rule(req: AlertRuleRequest,
                                _user: str = Depends(require_auth)):
        rid = app.state.alert_rule_store.add_rule(
            req.name, req.metric, req.operator, req.threshold,
            req.severity, req.silence_minutes, req.channel_ids, req.enabled)
        return {"id": rid}

    @app.put("/api/alert-rules/{rule_id}")
    async def update_alert_rule(rule_id: int, req: AlertRuleRequest,
                                _user: str = Depends(require_auth)):
        ok = app.state.alert_rule_store.update_rule(
            rule_id, name=req.name, metric=req.metric, operator=req.operator,
            threshold=req.threshold, severity=req.severity,
            silence_minutes=req.silence_minutes,
            channel_ids=req.channel_ids, enabled=req.enabled)
        if not ok:
            raise HTTPException(404, "规则不存在")
        return {"ok": True}

    @app.delete("/api/alert-rules/{rule_id}")
    async def delete_alert_rule(rule_id: int, _user: str = Depends(require_auth)):
        if not app.state.alert_rule_store.delete_rule(rule_id):
            raise HTTPException(404, "规则不存在")
        return {"ok": True}

    # ---- 推送渠道 ----

    @app.get("/api/alert-channels")
    async def list_alert_channels(_user: str = Depends(require_auth)):
        channels = app.state.alert_rule_store.list_channels()
        return {"channels": [vars(c) for c in channels]}

    @app.post("/api/alert-channels")
    async def create_alert_channel(req: AlertChannelRequest,
                                   _user: str = Depends(require_auth)):
        cid = app.state.alert_rule_store.add_channel(
            req.name, req.type, req.config, req.enabled)
        return {"id": cid}

    @app.put("/api/alert-channels/{ch_id}")
    async def update_alert_channel(ch_id: int, req: AlertChannelRequest,
                                   _user: str = Depends(require_auth)):
        ok = app.state.alert_rule_store.update_channel(
            ch_id, name=req.name, type=req.type,
            config=req.config, enabled=req.enabled)
        if not ok:
            raise HTTPException(404, "渠道不存在")
        return {"ok": True}

    @app.delete("/api/alert-channels/{ch_id}")
    async def delete_alert_channel(ch_id: int,
                                   _user: str = Depends(require_auth)):
        if not app.state.alert_rule_store.delete_channel(ch_id):
            raise HTTPException(404, "渠道不存在")
        return {"ok": True}

    @app.post("/api/alert-channels/{ch_id}/test")
    async def test_alert_channel(ch_id: int, _user: str = Depends(require_auth)):
        ch = app.state.alert_rule_store.get_channel(ch_id)
        if not ch:
            raise HTTPException(404, "渠道不存在")
        payload = {
            "rule_name": "测试推送",
            "metric": "test", "metric_value": "—",
            "severity": "warning", "title": "KylinGuard 测试告警",
            "message": "这是一条来自 KylinGuard 的测试推送，渠道配置正确。",
        }
        ok, msg = await push_channel(ch, payload)
        return {"ok": ok, "message": msg}

    # ---- 告警历史 ----

    @app.get("/api/alert-history")
    async def list_alert_history(_user: str = Depends(require_auth)):
        entries = app.state.alert_rule_store.list_history()
        return {"history": [vars(e) for e in entries]}

    @app.delete("/api/alert-history")
    async def clear_alert_history(_user: str = Depends(require_auth)):
        app.state.alert_rule_store.clear_history()
        return {"ok": True}

    @app.get("/api/policies")
    async def list_policies(_user: str = Depends(require_auth)):
        return {"custom": app.state.policies.list(),
                "builtin": builtin_rules(), "kinds": list(KINDS)}

    @app.post("/api/policies")
    async def add_policy(req: PolicyRequest,
                         _user: str = Depends(require_auth)):
        try:
            pid = app.state.policies.add(req.kind, req.pattern, req.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": pid}

    @app.delete("/api/policies/{policy_id}")
    async def delete_policy(policy_id: int,
                            _user: str = Depends(require_auth)):
        if not app.state.policies.remove(policy_id):
            raise HTTPException(404, "策略不存在")
        return {"ok": True}

    if _FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=str(_FRONTEND_DIST), html=True),
                  name="frontend")

    return app
