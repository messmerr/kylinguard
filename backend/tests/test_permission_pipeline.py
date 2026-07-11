import time

import pytest

from kylinguard.audit import AuditError, AuditLog
from kylinguard.config import Settings
from kylinguard.models import (
    PermissionDecision,
    PermissionGrantScope,
    PermissionMode,
    PermissionResolution,
    PlannerOutput,
    ReviewVerdict,
    RiskLevel,
)
from kylinguard.permissions import PermissionRequests
from kylinguard.pipeline import Confirmations, Pipeline
from kylinguard.sessions import SessionStore


def plan(tool, arguments, risk="medium", purpose="记录信息"):
    return PlannerOutput.model_validate({
        "thought": "准备执行",
        "steps": [{
            "tool": tool, "arguments": arguments,
            "purpose": purpose, "risk": risk,
        }],
    })


FINAL = PlannerOutput(thought="完成", steps=[], final_answer="完成。")


class Planner:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def next_actions(self, conversation, on_delta=None, on_progress=None):
        return self.outputs.pop(0)


class Reviewer:
    def __init__(self, safe=True, risk=RiskLevel.LOW):
        self.safe = safe
        self.risk = risk
        self.calls = 0

    async def review(self, user_query, env_summary, action_desc, on_progress=None):
        self.calls += 1
        return ReviewVerdict(
            safe=self.safe, matches_intent=self.safe,
            risk=self.risk, reason="测试复核",
        )


class Tools:
    def __init__(self):
        self.calls = []

    def describe(self):
        return "- files.write_file(path, content)\n- run_command.run_batch(commands, operators)"

    async def call(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        return '{"operation":"ok"}'


async def snapshot():
    return {"disk": "ok"}, 0.0


def make_pipeline(tmp_path, outputs, mode=PermissionMode.ASK, roots=None,
                  reviewer=None):
    db = str(tmp_path / "kg.db")
    settings = Settings(_env_file=None, db_path=db, confirm_timeout=2)
    audit = AuditLog(db)
    sessions = SessionStore(db)
    expiry = (time.time() + 300
              if mode in {PermissionMode.TRUSTED_WORKSPACE,
                          PermissionMode.FULL_ACCESS} else None)
    sessions.create(
        "s1", "测试", permission_mode=mode,
        trusted_roots=roots or [], permission_expires_at=expiry,
        updated_by="admin",
    )
    requests = PermissionRequests()
    tools = Tools()
    pipeline = Pipeline(
        settings=settings, audit=audit, tools=tools,
        planner=Planner(outputs), reviewer=reviewer or Reviewer(),
        confirmations=Confirmations(), snapshot_fn=snapshot,
        session_store=sessions, permission_requests=requests,
    )
    return pipeline, tools, requests, sessions, audit


async def collect(pipeline, on_event=None):
    events = []

    async def emit(event):
        events.append(event)
        if on_event:
            await on_event(event)

    await pipeline.handle("s1", "请在指定目录写一份文档", emit)
    return events


async def test_默认模式文件写入请求权限且允许后执行(tmp_path):
    target = str(tmp_path / "notes.md")
    pipeline, tools, requests, sessions, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": target, "content": "hello", "create_only": True,
        }), FINAL])

    async def approve(event):
        if event["type"] == "permission_request":
            grant = sessions.add_grant(
                "s1",
                scope=PermissionGrantScope.ONCE,
                action_fingerprint=event["action"]["fingerprint"],
                capability=event["capability"],
                resource=event["resource"],
                context_version=event["context_version"],
                granted_by="admin",
                expires_at=time.time() + 60,
            )
            requests.resolve(PermissionResolution(
                request_id=event["request_id"],
                decision=PermissionDecision.ALLOW_ONCE,
                operator="admin",
                context_version=event["context_version"],
                grant_id=grant.id,
            ))

    events = await collect(pipeline, approve)
    types = [event["type"] for event in events]
    assert "permission_context" in types
    assert "permission_request" in types
    assert "permission_result" in types
    assert tools.calls[0][0:2] == ("files", "write_file")


async def test_人工批准后若权限被收回_执行前复验会中止(tmp_path):
    target = str(tmp_path / "notes.md")
    pipeline, tools, requests, sessions, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": target, "content": "hello", "create_only": True,
        }), FINAL])

    async def approve_then_revoke(event):
        if event["type"] != "permission_request":
            return
        grant = sessions.add_grant(
            "s1", scope=PermissionGrantScope.ONCE,
            action_fingerprint=event["action"]["fingerprint"],
            capability=event["capability"], resource=event["resource"],
            context_version=1, granted_by="admin",
            expires_at=time.time() + 60,
        )
        requests.resolve(PermissionResolution(
            request_id=event["request_id"],
            decision=PermissionDecision.ALLOW_ONCE,
            operator="admin", context_version=1, grant_id=grant.id,
        ))
        sessions.set_permissions(
            "s1", mode=PermissionMode.READ_ONLY, trusted_roots=[],
            expires_at=None, expected_version=1, updated_by="admin",
        )

    events = await collect(pipeline, approve_then_revoke)
    assert tools.calls == []
    assert "execution_authorization_failed" in {
        event["type"] for event in events
    }


async def test_一次授权消费与执行授权审计原子提交(tmp_path, monkeypatch):
    target = str(tmp_path / "notes.md")
    pipeline, tools, requests, sessions, audit = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": target, "content": "hello", "create_only": True,
        }), FINAL])
    issued_grant = None

    async def approve(event):
        nonlocal issued_grant
        if event["type"] != "permission_request":
            return
        issued_grant = sessions.add_grant(
            "s1", scope=PermissionGrantScope.ONCE,
            action_fingerprint=event["action"]["fingerprint"],
            capability=event["capability"], resource=event["resource"],
            context_version=1, granted_by="admin",
            expires_at=time.time() + 60,
        )
        requests.resolve(PermissionResolution(
            request_id=event["request_id"],
            decision=PermissionDecision.ALLOW_ONCE,
            operator="admin", context_version=1,
            grant_id=issued_grant.id,
        ))

    original_append = audit.append

    def fail_authorization(session_id, event_type, payload, **kwargs):
        if event_type == "execution_authorized":
            raise AuditError("模拟审计失败")
        return original_append(session_id, event_type, payload, **kwargs)

    monkeypatch.setattr(audit, "append", fail_authorization)
    with pytest.raises(AuditError):
        await collect(pipeline, approve)
    assert tools.calls == []
    grant = sessions.list_grants("s1", active_only=False)[0]
    assert grant.id == issued_grant.id
    assert grant.consumed_at is None


async def test_可信目录内创建文档无需逐项询问(tmp_path):
    root = tmp_path / "docs"
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": str(root / "notes.md"), "content": "hello",
        }), FINAL], mode=PermissionMode.TRUSTED_WORKSPACE, roots=[str(root)])
    events = await collect(pipeline)
    assert "permission_request" not in [event["type"] for event in events]
    assert tools.calls


async def test_只读模式拒绝写入并告诉模型不要重试(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": str(tmp_path / "notes.md"), "content": "hello",
        }), FINAL], mode=PermissionMode.READ_ONLY)
    events = await collect(pipeline)
    assert tools.calls == []
    verification = next(e for e in events if e["type"] == "verification")
    assert verification["decision"]["action"] == "deny"


async def test_完全访问跳过人工确认但reviewer与硬红线仍可拒绝(tmp_path):
    pipeline0, tools0, _, _, _ = make_pipeline(
        tmp_path / "safe", [plan("files.write_file", {
            "path": str(tmp_path / "safe" / "notes.md"), "content": "hello",
        }), FINAL], mode=PermissionMode.FULL_ACCESS)
    await collect(pipeline0)
    assert tools0.calls

    unsafe_reviewer = Reviewer(safe=False, risk=RiskLevel.HIGH)
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": str(tmp_path / "notes.md"), "content": "hello",
        }), FINAL], mode=PermissionMode.FULL_ACCESS, reviewer=unsafe_reviewer)
    await collect(pipeline)
    assert tools.calls == []

    pipeline2, tools2, _, _, _ = make_pipeline(
        tmp_path / "second", [plan("run_command.run_command", {
            "command": "rm -rf /",
        }, risk="high"), FINAL], mode=PermissionMode.FULL_ACCESS)
    await collect(pipeline2)
    assert tools2.calls == []


async def test_只读分号命令自动改写为无shell批处理(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [plan("run_command.run_command", {
            "command": "ps aux; free -m",
        }, risk="low", purpose="查看资源"), FINAL])
    events = await collect(pipeline)
    rewrite = next(e for e in events if e["type"] == "step_rewrite")
    assert rewrite["outcome"] == "rewritten"
    assert tools.calls == [("run_command", "run_batch", {
        "commands": [["ps", "aux"], ["free", "-m"]],
        "operators": [";"],
    })]


async def test_重定向被归类为可改写而非危险并停止相同重试(tmp_path):
    first = plan("run_command.run_command", {
        "command": f"echo hello > {tmp_path / 'notes.md'}",
    })
    second = plan("run_command.run_command", {
        "command": f"printf hello > {tmp_path / 'notes.md'}",
    })
    pipeline, tools, _, _, _ = make_pipeline(tmp_path, [first, second, FINAL])
    events = await collect(pipeline)
    rewrites = [e for e in events if e["type"] == "step_rewrite"]
    assert rewrites[0]["outcome"] == "rewrite_required"
    assert tools.calls == []
