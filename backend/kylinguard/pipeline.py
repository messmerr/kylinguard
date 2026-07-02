"""五阶段安全流水线编排：感知→规划→校验→受限执行→溯源。

- 每个事件先落审计链再对外发射；审计失败（AuditError）直接上抛中止任务。
- 迭代规划：把每轮工具结果喂回会话，直至 final_answer 或轮数上限。
- 中高危步骤经 Confirmations 挂起等待管理员决断，超时按拒绝。
"""
import asyncio
import json
import uuid

from kylinguard.audit import AuditLog
from kylinguard.config import Settings
from kylinguard.gate import decide
from kylinguard.models import (
    GateAction, PlanStep, RuleDecision, RuleVerdict,
)
from kylinguard.mcp_client import split_qualified
from kylinguard.planner import PlanningError, build_system_prompt
from kylinguard.registry import get_meta
from kylinguard.rules import check_command
from kylinguard.snapshot import collect_snapshot, format_snapshot


class Confirmations:
    """挂起中的人工确认：confirm_id → Future[bool]。"""

    def __init__(self):
        self._pending: dict[str, asyncio.Future] = {}

    def create(self) -> tuple[str, asyncio.Future]:
        confirm_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[confirm_id] = fut
        return confirm_id, fut

    def resolve(self, confirm_id: str, approved: bool) -> bool:
        fut = self._pending.pop(confirm_id, None)
        if fut is None or fut.done():
            return False
        fut.set_result(approved)
        return True


class Pipeline:
    def __init__(self, settings: Settings, audit: AuditLog, tools,
                 planner, reviewer, confirmations: Confirmations,
                 snapshot_fn=collect_snapshot):
        self._settings = settings
        self._audit = audit
        self._tools = tools
        self._planner = planner
        self._reviewer = reviewer
        self.confirmations = confirmations
        self._snapshot_fn = snapshot_fn

    async def handle(self, session_id: str, user_query: str, emit) -> None:
        async def record(event_type: str, payload: dict):
            h = self._audit.append(session_id, event_type, payload)
            await emit({"type": event_type, "session_id": session_id,
                        "hash": h, **payload})

        await record("user_query", {"query": user_query})

        # ① 感知
        snapshot = await self._snapshot_fn()
        await record("snapshot", {"snapshot": snapshot})
        env_summary = format_snapshot(snapshot, per_item=1500)

        conversation = [
            {"role": "system",
             "content": build_system_prompt(self._tools.describe())},
            {"role": "user",
             "content": f"管理员指令：{user_query}\n\n当前系统快照：\n{env_summary}"},
        ]

        for round_no in range(self._settings.max_iterations):
            # ② 规划
            try:
                plan = await self._planner.next_actions(conversation)
            except PlanningError as e:
                await record("final_answer", {"answer": str(e), "aborted": True})
                return
            await record("plan", {"round": round_no, **plan.model_dump()})

            if not plan.steps:
                await record("final_answer",
                             {"answer": plan.final_answer or "（模型未给出结论）",
                              "aborted": False})
                return

            observations = []
            for step in plan.steps:
                observations.append(await self._run_step(
                    user_query, env_summary, step, record))

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
        })

    async def _run_step(self, user_query: str, env_summary: str,
                        step: PlanStep, record) -> str:
        # ③ 校验：三道闸
        try:
            server, tool = split_qualified(step.tool)
        except ValueError as e:
            return f"步骤 {step.tool!r} 无效：{e}"
        meta = get_meta(server, tool)

        if meta.dynamic:
            command = str(step.arguments.get("command", ""))
            rule = check_command(command)
            action_desc = f"执行命令：{command}（声称目的：{step.purpose}）"
        else:
            rule = RuleVerdict(decision=RuleDecision.REVIEW,
                               reason="结构化插件工具，参数已受插件约束")
            action_desc = (f"调用工具 {step.tool}，参数 "
                           f"{json.dumps(step.arguments, ensure_ascii=False)}"
                           f"（声称目的：{step.purpose}）")

        review = await self._reviewer.review(user_query, env_summary, action_desc)
        decision = decide(meta, rule, review, step.risk)
        await record("verification", {
            "step": step.model_dump(), "rule": rule.model_dump(),
            "review": review.model_dump(), "decision": decision.model_dump(),
        })

        if decision.action == GateAction.DENY:
            return f"步骤 {step.tool} 被安全闸门拒绝：{decision.reason}"

        if decision.action in (GateAction.CONFIRM, GateAction.DOUBLE_CONFIRM):
            confirm_id, fut = self.confirmations.create()
            await record("confirm_request", {
                "confirm_id": confirm_id, "step": step.model_dump(),
                "decision": decision.model_dump(),
            })
            try:
                approved = await asyncio.wait_for(
                    fut, timeout=self._settings.confirm_timeout)
            except asyncio.TimeoutError:
                self.confirmations.resolve(confirm_id, False)  # 清理挂起项
                approved = False
            await record("confirm_result",
                         {"confirm_id": confirm_id, "approved": approved})
            if not approved:
                return f"步骤 {step.tool} 未获管理员批准（拒绝或超时），已跳过"

        # ④ 受限执行（经 MCP 插件进程）
        try:
            output = await self._tools.call(server, tool, step.arguments)
        except Exception as e:
            output = f"[工具调用失败] {e}"
        # ⑤ 溯源
        await record("execution",
                     {"step": step.model_dump(), "output": output[:8000]})
        return f"步骤 {step.tool} 输出：\n{output[:4000]}"
