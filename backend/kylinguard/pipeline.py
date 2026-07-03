"""五阶段安全流水线编排：感知→规划→校验→受限执行→溯源。

- 每个事件先落审计链再对外发射；审计失败（AuditError）直接上抛中止任务。
  例外：assistant_delta 流式增量只走 UI 不逐条入审计——整轮完整文本
  在 plan/final_answer 事件里落链，审计完整性不受影响。
- 多轮对话：conversation 按 session_id 常驻内存；服务重启后从审计链
  摘要重建（历史指令与结论，工具细节不还原）。
- 迭代规划：把每轮工具结果喂回会话，直至 final_answer 或轮数上限。
- 中高危步骤经 Confirmations 挂起等待管理员决断，超时按拒绝。
"""
import asyncio
import json
import time
import uuid

from kylinguard.audit import AuditLog
from kylinguard.config import Settings
from kylinguard.gate import decide
from kylinguard.intent import screen_user_intent
from kylinguard.models import (
    GateAction, PlanStep, RuleDecision, RuleVerdict,
)
from kylinguard.mcp_client import split_qualified
from kylinguard.planner import PlanningError, build_system_prompt
from kylinguard.registry import get_meta
from kylinguard.rules import check_command
from kylinguard.snapshot import collect_snapshot, format_snapshot


async def _fresh_snapshot() -> tuple[dict[str, str], float]:
    """默认快照源：即时采集（生产环境注入 SnapshotCache.get 走缓存）。"""
    return await collect_snapshot(), 0.0


class Confirmations:
    """挂起中的人工确认：confirm_id → Future[(approved, operator)]。

    operator 是做出决断的管理员账号，随 confirm_result 写入审计链
    （谁在何时批准了哪条中高危操作）。
    """

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}

    def create(self) -> tuple[str, asyncio.Future]:
        confirm_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[confirm_id] = fut
        return confirm_id, fut

    def resolve(self, confirm_id: str, approved: bool,
                operator: str = "") -> bool:
        fut = self._pending.pop(confirm_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result((approved, operator))
        return True


class Pipeline:
    def __init__(self, settings: Settings, audit: AuditLog, tools,
                 planner, reviewer, confirmations: Confirmations,
                 snapshot_fn=_fresh_snapshot, policy_store=None):
        self._settings = settings
        self._audit = audit
        self._tools = tools
        self._planner = planner
        self._reviewer = reviewer
        self.confirmations = confirmations
        self._snapshot_fn = snapshot_fn
        self._policy_store = policy_store  # 鸭子类型：extra() -> ExtraPolicies
        self._conversations: dict[str, list[dict]] = {}

    def _get_conversation(self, session_id: str) -> list[dict]:
        conv = self._conversations.get(session_id)
        if conv is None:
            conv = [{"role": "system",
                     "content": build_system_prompt(self._tools.describe())}]
            # 重启恢复：从审计链摘要重建（指令与结论对，工具细节不还原）
            for ev in self._audit.events(session_id):
                if ev["event_type"] == "user_query":
                    conv.append({"role": "user",
                                 "content": f"管理员指令：{ev['payload']['query']}"})
                elif ev["event_type"] == "final_answer":
                    conv.append({"role": "assistant",
                                 "content": ev["payload"]["answer"]})
            self._conversations[session_id] = conv
        return conv

    async def handle(self, session_id: str, user_query: str, emit) -> None:
        started = time.monotonic()

        async def record(event_type: str, payload: dict):
            h = self._audit.append(session_id, event_type, payload)
            await emit({"type": event_type, "session_id": session_id,
                        "hash": h, **payload})

        async def phase(name: str, **extra):
            # 阶段指示：纯 UI 事件（不入审计），让内部工作对用户可感
            await emit({"type": "phase", "session_id": session_id,
                        "phase": name, **extra})

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        conversation = self._get_conversation(session_id)
        await record("user_query", {"query": user_query})

        intent = screen_user_intent(user_query)
        if intent.decision == RuleDecision.DENY:
            await record("intent_filter", {"decision": intent.model_dump()})
            await record("final_answer", {
                "answer": f"请求已被安全意图校验器拒绝：{intent.reason}",
                "aborted": True,
                "elapsed_ms": elapsed_ms(),
            })
            return

        # ① 感知（走缓存，collected_ago_seconds = 快照距采集的秒数）
        snapshot, age = await self._snapshot_fn()
        await record("snapshot", {"snapshot": snapshot,
                                  "collected_ago_seconds": round(age, 1)})
        env_summary = format_snapshot(snapshot, per_item=1500)

        conversation.append(
            {"role": "user",
             "content": f"管理员指令：{user_query}\n\n当前系统快照：\n{env_summary}"})

        for round_no in range(self._settings.max_iterations):
            # ② 规划（分析文本经 assistant_delta 流式外发，不逐条入审计）
            await phase("planning", round=round_no)

            async def on_delta(text: str, _round=round_no):
                await emit({"type": "assistant_delta",
                            "session_id": session_id,
                            "round": _round, "text": text})

            try:
                plan = await self._planner.next_actions(conversation,
                                                        on_delta=on_delta)
            except PlanningError as e:
                await record("final_answer", {"answer": str(e), "aborted": True,
                                              "elapsed_ms": elapsed_ms()})
                return
            # step_id 在规划落审计前生成：前端按它把校验/确认/执行聚合到
            # 同一步骤行，审计回放（M2）按它分组
            step_ids = [uuid.uuid4().hex[:12] for _ in plan.steps]
            await record("plan", {
                "round": round_no, "thought": plan.thought,
                "steps": [{**s.model_dump(), "step_id": sid}
                          for s, sid in zip(plan.steps, step_ids)],
                "final_answer": plan.final_answer,
            })

            if not plan.steps:
                answer = plan.final_answer or "（模型未给出结论）"
                conversation.append({"role": "assistant", "content": answer})
                await record("final_answer", {"answer": answer,
                                              "aborted": False,
                                              "elapsed_ms": elapsed_ms()})
                return

            observations = []
            for step, step_id in zip(plan.steps, step_ids):
                observations.append(await self._run_step(
                    user_query, env_summary, step, step_id, record, phase))

            conversation.append({"role": "assistant",
                                 "content": plan.model_dump_json()})
            conversation.append({
                "role": "user",
                "content": "各步骤执行结果：\n\n" + "\n\n".join(observations)
                           + "\n\n请基于以上结果继续规划，或给出最终结论。",
            })

        await record("final_answer", {
            "answer": f"迭代轮数达到上限（{self._settings.max_iterations}），"
                      "任务中止。请缩小问题范围后重试。",
            "aborted": True,
            "elapsed_ms": elapsed_ms(),
        })

    async def _run_step(self, user_query: str, env_summary: str,
                        step: PlanStep, step_id: str, record, phase) -> str:
        # ③ 校验：三道闸
        try:
            server, tool = split_qualified(step.tool)
        except ValueError as e:
            return f"步骤 {step.tool!r} 无效：{e}"
        meta = get_meta(server, tool)

        if meta.dynamic:
            command = str(step.arguments.get("command", ""))
            extra = self._policy_store.extra() if self._policy_store else None
            rule = check_command(command, extra=extra)
            action_desc = f"执行命令：{command}（声称目的：{step.purpose}）"
        else:
            rule = RuleVerdict(decision=RuleDecision.REVIEW,
                               reason="结构化插件工具，参数已受插件约束")
            action_desc = (f"调用工具 {step.tool}，参数 "
                           f"{json.dumps(step.arguments, ensure_ascii=False)}"
                           f"（声称目的：{step.purpose}）")

        await phase("reviewing", step_id=step_id, tool=step.tool)
        review = await self._reviewer.review(user_query, env_summary, action_desc)
        decision = decide(meta, rule, review, step.risk)
        await record("verification", {
            "step_id": step_id,
            "step": step.model_dump(), "rule": rule.model_dump(),
            "review": review.model_dump(), "decision": decision.model_dump(),
        })

        if decision.action == GateAction.DENY:
            return f"步骤 {step.tool} 被安全闸门拒绝：{decision.reason}"

        if decision.action in (GateAction.CONFIRM, GateAction.DOUBLE_CONFIRM):
            confirm_id, fut = self.confirmations.create()
            await record("confirm_request", {
                "confirm_id": confirm_id, "step_id": step_id,
                "step": step.model_dump(),
                "decision": decision.model_dump(),
            })
            try:
                approved, operator = await asyncio.wait_for(
                    fut, timeout=self._settings.confirm_timeout)
            except asyncio.TimeoutError:
                self.confirmations.resolve(confirm_id, False)  # 清理挂起项
                approved, operator = False, "(超时)"
            await record("confirm_result",
                         {"confirm_id": confirm_id, "step_id": step_id,
                          "approved": approved, "operator": operator})
            if not approved:
                return f"步骤 {step.tool} 未获管理员批准（拒绝或超时），已跳过"

        # ④ 受限执行（经 MCP 插件进程）
        started = time.monotonic()
        try:
            output = await self._tools.call(server, tool, step.arguments)
        except Exception as e:
            output = f"[工具调用失败] {e}"
        duration_ms = int((time.monotonic() - started) * 1000)
        # ⑤ 溯源
        await record("execution",
                     {"step_id": step_id, "step": step.model_dump(),
                      "duration_ms": duration_ms, "output": output[:8000]})
        return f"步骤 {step.tool} 输出：\n{output[:4000]}"
