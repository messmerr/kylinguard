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
    ToolMeta,
)
from kylinguard.permissions import PermissionRequests
from kylinguard.pipeline import Confirmations, Pipeline
from kylinguard.policy import PolicyStore
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
                  reviewer=None, settings=None, workspace_root="", tools=None):
    db = str(tmp_path / "kg.db")
    settings = settings or Settings(
        _env_file=None, db_path=db, confirm_timeout=2)
    audit = AuditLog(db)
    sessions = SessionStore(db)
    sessions.create("s1", "测试", workspace_root=workspace_root)
    if mode == PermissionMode.FULL_ACCESS:
        sessions.set_session_full_access(
            "s1", enabled=True, expected_version=1,
            updated_by="admin", execution_profile="sha256:test-profile",
        )
    elif mode != PermissionMode.ASK or roots:
        sessions.set_permission_settings(
            mode=mode, auto_review_roots=roots or [],
            expected_version=1, updated_by="admin",
        )
    requests = PermissionRequests()
    tools = tools or Tools()
    pipeline = Pipeline(
        settings=settings, audit=audit, tools=tools,
        planner=Planner(outputs), reviewer=reviewer or Reviewer(),
        confirmations=Confirmations(), snapshot_fn=snapshot,
        session_store=sessions, permission_requests=requests,
    )
    return pipeline, tools, requests, sessions, audit


class CustomRiskTools(Tools):
    def __init__(self, risk=RiskLevel.LOW, source="administrator"):
        super().__init__()
        self.meta = ToolMeta(
            server="external", tool="lookup", risk=risk,
            custom=True, risk_source=source,
            description="第三方查询工具",
        )

    def tool_security_context(self, qualified):
        assert qualified == "external.lookup"
        return self.meta, f"sha256:{self.meta.risk.value}"

    async def call_checked(
        self, server, tool, arguments, *, expected_identity="",
    ):
        assert expected_identity == f"sha256:{self.meta.risk.value}"
        return await self.call(server, tool, arguments)


class FailingReviewer:
    async def review(self, user_query, env_summary, action_desc, on_progress=None):
        raise RuntimeError("review service unavailable")


def test_流水线kill_switch会把遗留完全访问视为确认模式(tmp_path):
    settings = Settings(
        _env_file=None,
        db_path=str(tmp_path / "kg.db"),
        confirm_timeout=2,
        allow_full_access=False,
    )
    pipeline, _, _, sessions, _ = make_pipeline(
        tmp_path,
        [FINAL],
        mode=PermissionMode.FULL_ACCESS,
        settings=settings,
    )

    effective = pipeline._effective_permission_context(
        sessions.get_permissions("s1"))
    assert effective.mode == PermissionMode.ASK


async def collect(pipeline, on_event=None):
    events = []

    async def emit(event):
        events.append(event)
        if on_event:
            await on_event(event)

    await pipeline.handle("s1", "请在指定目录写一份文档", emit)
    return events


async def test_管理员低风险mcp分级可在只读模式自动执行并解释来源(tmp_path):
    tools = CustomRiskTools()
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("external.lookup", {"query": "status"}, risk="low",
              purpose="读取第三方状态"), FINAL],
        mode=PermissionMode.READ_ONLY,
        tools=tools,
    )

    events = await collect(pipeline)
    verification = next(
        event for event in events if event["type"] == "verification"
    )

    assert verification["decision"]["action"] == "auto"
    assert verification["tool_risk"] == {
        "baseline": "low", "source": "administrator", "custom": True,
    }
    assert tools.calls == [("external", "lookup", {"query": "status"})]


async def test_未分级mcp在只读模式保持平台默认高风险(tmp_path):
    tools = CustomRiskTools(
        risk=RiskLevel.HIGH, source="platform_default",
    )
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("external.lookup", {"query": "status"}, risk="low",
              purpose="读取第三方状态"), FINAL],
        mode=PermissionMode.READ_ONLY,
        tools=tools,
    )

    events = await collect(pipeline)
    verification = next(
        event for event in events if event["type"] == "verification"
    )

    assert verification["decision"]["action"] == "deny"
    assert verification["decision"]["risk"] == "high"
    assert verification["tool_risk"]["source"] == "platform_default"
    assert tools.calls == []


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
        sessions.set_permission_settings(
            mode=PermissionMode.READ_ONLY, auto_review_roots=[],
            expected_version=1, updated_by="admin",
        )

    events = await collect(pipeline, approve_then_revoke)
    assert tools.calls == []
    assert "execution_authorization_failed" in {
        event["type"] for event in events
    }


async def test_等待授权期间策略变化会在工具启动前中止(tmp_path):
    command = "some-new-ops-tool --check"
    pipeline, tools, requests, sessions, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": command,
        }, purpose="检查状态"), FINAL],
    )
    policies = PolicyStore(str(tmp_path / "kg.db"))
    pipeline._policy_store = policies

    async def change_policy_then_approve(event):
        if event["type"] != "permission_request":
            return
        policies.add("blacklist", r"\bsome-new-ops-tool\b", "刚加入的策略")
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

    events = await collect(pipeline, change_policy_then_approve)
    failure = next(
        event for event in events
        if event["type"] == "execution_authorization_failed"
    )
    assert failure["code"] == "policy_changed_before_execution"
    assert tools.calls == []
    policies.close()


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


async def test_自动审核范围内创建文档无需逐项询问(tmp_path):
    root = tmp_path / "docs"
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": str(root / "notes.md"), "content": "hello",
        }), FINAL], mode=PermissionMode.AUTO_REVIEW, roots=[str(root)])
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


async def test_完全访问跳过人工确认与reviewer并保留真实shell能力(tmp_path):
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
    events = await collect(pipeline)
    assert tools.calls
    assert "review_bypassed" in {event["type"] for event in events}
    assert unsafe_reviewer.calls == 0

    pipeline2, tools2, _, _, _ = make_pipeline(
        tmp_path / "second", [plan("run_command.run_command", {
            "command": "rm -rf /",
        }, risk="high"), FINAL], mode=PermissionMode.FULL_ACCESS)
    await collect(pipeline2)
    # 完全访问不靠字符串规则伪装成 shell 沙箱；最终能力由 OS 身份决定。
    assert tools2.calls == [("run_command", "run_command", {
        "command": "rm -rf /",
    })]

    ordinary_target = tmp_path / "ordinary" / "generated"
    pipeline3, tools3, _, _, _ = make_pipeline(
        tmp_path / "third", [plan("run_command.run_command", {
            "command": f"rm -rf {ordinary_target}",
        }, risk="high"), FINAL], mode=PermissionMode.FULL_ACCESS)
    await collect(pipeline3)
    assert tools3.calls == [("run_command", "run_command", {
        "command": f"rm -rf {ordinary_target}",
    })]


async def test_自动审核在reviewer故障时降级为人工确认(tmp_path):
    root = tmp_path / "docs"
    pipeline, tools, requests, sessions, _ = make_pipeline(
        tmp_path, [plan("files.write_file", {
            "path": str(root / "notes.md"), "content": "hello",
        }), FINAL], mode=PermissionMode.AUTO_REVIEW, roots=[str(root)],
        reviewer=FailingReviewer(),
    )

    async def approve(event):
        if event["type"] != "permission_request":
            return
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

    assert "review_failed" in {event["type"] for event in events}
    assert "permission_request" in {event["type"] for event in events}
    assert tools.calls


async def test_完全访问把复合shell原样交给通用终端(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [plan("run_command.run_command", {
            "command": "ps aux; free -m",
        }, risk="low", purpose="查看资源"), FINAL],
        mode=PermissionMode.FULL_ACCESS)
    events = await collect(pipeline)
    assert "step_rewrite" not in {event["type"] for event in events}
    assert tools.calls == [("run_command", "run_command", {
        "command": "ps aux; free -m",
    })]


async def test_完全访问支持重定向且不同命令都可执行(tmp_path):
    first = plan("run_command.run_command", {
        "command": f"echo hello > {tmp_path / 'notes.md'}",
    })
    second = plan("run_command.run_command", {
        "command": f"printf hello > {tmp_path / 'notes.md'}",
    })
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [first, second, FINAL], mode=PermissionMode.FULL_ACCESS)
    events = await collect(pipeline)
    assert "step_rewrite" not in {event["type"] for event in events}
    assert tools.calls == [
        ("run_command", "run_command", first.steps[0].arguments),
        ("run_command", "run_command", second.steps[0].arguments),
    ]


async def test_完全访问用结构化工具访问控制面路径不会产生路线依赖(tmp_path):
    db_path = str(tmp_path / "kg.db")
    step = plan("files.write_file", {
        "path": db_path,
        "content": "authorized replacement",
        "create_only": False,
    }, risk="high", purpose="按管理员要求修改控制文件")
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path, [step, FINAL], mode=PermissionMode.FULL_ACCESS,
    )

    events = await collect(pipeline)
    verification = next(
        event for event in events if event["type"] == "verification"
    )
    assert verification["decision"]["action"] == "auto"
    assert tools.calls == [
        ("files", "write_file", step.steps[0].arguments),
    ]


async def test_只读白名单命令自动改走无shell的精确argv(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "pwd", "cwd": str(tmp_path),
        }, risk="low", purpose="查看目录"), FINAL],
        mode=PermissionMode.READ_ONLY,
    )

    events = await collect(pipeline)
    rewrite = next(event for event in events if event["type"] == "step_rewrite")
    assert rewrite["outcome"] == "readonly_argv"
    assert tools.calls == [("run_command", "run_batch", {
        "commands": [["pwd"]], "cwd": str(tmp_path),
    })]


@pytest.mark.parametrize(("command", "expected"), [
    (
        "ps aux && uptime",
        {
            "commands": [["ps", "aux"], ["uptime"]],
            "operators": ["&&"],
        },
    ),
    (
        "grep 'a&b' report.txt || echo 'not found'",
        {
            "commands": [
                ["grep", "a&b", "report.txt"],
                ["echo", "not found"],
            ],
            "operators": ["||"],
        },
    ),
    (
        "pwd; free -m",
        {
            "commands": [["pwd"], ["free", "-m"]],
            "operators": [";"],
        },
    ),
    (
        "echo 'https://example.test/search?q=a&lang=zh'",
        {
            "commands": [[
                "echo", "https://example.test/search?q=a&lang=zh",
            ]],
        },
    ),
])
async def test_只读命令链与引号内元字符自动改写为run_batch(
    tmp_path, command, expected,
):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": command,
        }, risk="low", purpose="读取系统状态"), FINAL],
        mode=PermissionMode.READ_ONLY,
    )

    events = await collect(pipeline)

    rewrite = next(event for event in events if event["type"] == "step_rewrite")
    assert rewrite["outcome"] == "readonly_argv"
    assert tools.calls == [("run_command", "run_batch", expected)]


async def test_单个后台运算符仍请求权限且不会改写为run_batch(tmp_path):
    pipeline, tools, requests, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "ps aux &",
        }, risk="low", purpose="后台查看进程"), FINAL],
        mode=PermissionMode.ASK,
    )

    async def deny(event):
        if event["type"] != "permission_request":
            return
        requests.resolve(PermissionResolution(
            request_id=event["request_id"],
            decision=PermissionDecision.DENY,
            operator="admin",
            context_version=event["context_version"],
        ))

    events = await collect(pipeline, deny)
    verification = next(
        event for event in events if event["type"] == "verification"
    )

    assert verification["rule"]["decision"] == "review"
    assert verification["rule"]["matched_rule"] == "shell_expression"
    assert verification["decision"]["action"] == "confirm"
    assert "permission_request" in {event["type"] for event in events}
    assert "step_rewrite" not in {event["type"] for event in events}
    assert tools.calls == []


async def test_含写操作的命令链不会因首段只读而自动改写(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "pwd && rm /tmp/old.txt",
        }, risk="low", purpose="读取后清理"), FINAL],
        mode=PermissionMode.READ_ONLY,
    )

    events = await collect(pipeline)
    verification = next(
        event for event in events if event["type"] == "verification"
    )

    assert verification["decision"]["action"] == "deny"
    assert "step_rewrite" not in {event["type"] for event in events}
    assert tools.calls == []


async def test_后台运算符后的危险命令提升为高风险双确认(tmp_path):
    pipeline, tools, requests, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "pwd & rm /tmp/old.txt",
        }, risk="low", purpose="读取后删除"), FINAL],
        mode=PermissionMode.ASK,
    )

    async def deny(event):
        if event["type"] != "permission_request":
            return
        requests.resolve(PermissionResolution(
            request_id=event["request_id"],
            decision=PermissionDecision.DENY,
            operator="admin",
            context_version=event["context_version"],
        ))

    events = await collect(pipeline, deny)
    verification = next(
        event for event in events if event["type"] == "verification"
    )

    assert verification["rule"]["matched_rule"] == "shell_dangerous_segment"
    assert verification["decision"]["risk"] == "high"
    assert verification["decision"]["action"] == "double_confirm"
    assert tools.calls == []


@pytest.mark.parametrize("command", [
    "diff --output=/tmp/result a b",
    "ss -K dst 192.0.2.1",
    "journalctl --vacuum-time=1s",
    "lastlog --clear --user demo",
])
async def test_只读模式不会自动执行白名单命令的写型参数(
    tmp_path, command,
):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": command,
        }, risk="low", purpose="检查系统"), FINAL],
        mode=PermissionMode.READ_ONLY,
    )

    events = await collect(pipeline)
    verification = next(
        event for event in events if event["type"] == "verification"
    )
    assert verification["decision"]["action"] == "deny"
    assert tools.calls == []


async def test_只读模式允许run_batch参数中的字面shell元字符(tmp_path):
    arguments = {
        "commands": [["grep", "a|b", "report(1).txt"]],
    }
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_batch", arguments,
              risk="low", purpose="搜索字面文本"), FINAL],
        mode=PermissionMode.READ_ONLY,
    )

    events = await collect(pipeline)
    verification = next(
        event for event in events if event["type"] == "verification"
    )
    assert verification["decision"]["action"] == "auto"
    assert tools.calls == [("run_command", "run_batch", arguments)]


async def test_完全访问保留简单命令的原始bash语义(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "pwd", "cwd": str(tmp_path),
        }, risk="low", purpose="查看目录"), FINAL],
        mode=PermissionMode.FULL_ACCESS,
    )

    events = await collect(pipeline)
    assert "step_rewrite" not in {event["type"] for event in events}
    assert tools.calls == [("run_command", "run_command", {
        "command": "pwd", "cwd": str(tmp_path),
    })]


async def test_会话工作目录成为终端默认cwd但不是命令沙箱(tmp_path):
    workspace = str(tmp_path / "opened-project")
    (tmp_path / "opened-project").mkdir()
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "git status --short",
        }, risk="low", purpose="查看项目状态"), FINAL],
        mode=PermissionMode.FULL_ACCESS,
        workspace_root=workspace,
    )

    events = await collect(pipeline)
    user_query = next(event for event in events if event["type"] == "user_query")
    assert user_query["workspace_root"] == workspace
    assert tools.calls == [("run_command", "run_command", {
        "command": "git status --short", "cwd": workspace,
    })]


async def test_只读模式不会让bash_ansi引用绕过资源确认(tmp_path):
    pipeline, tools, _, _, _ = make_pipeline(
        tmp_path,
        [plan("run_command.run_command", {
            "command": "cat $'.env'", "cwd": str(tmp_path),
        }, risk="low", purpose="读取文件"), FINAL],
        mode=PermissionMode.READ_ONLY,
    )

    events = await collect(pipeline)
    verification = next(event for event in events if event["type"] == "verification")
    assert verification["decision"]["action"] == "deny"
    assert tools.calls == []
