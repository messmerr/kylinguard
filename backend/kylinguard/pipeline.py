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
from kylinguard.llm import LLMError, PublicError, public_error
from kylinguard.models import (
    GateAction, PlanStep, RuleDecision, RuleVerdict,
)
from kylinguard.mcp_client import ToolCallError, split_qualified
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
                elif (ev["event_type"] == "final_answer"
                      and ev["payload"].get("outcome")
                      not in {"failed", "cancelled"}):
                    conv.append({"role": "assistant",
                                 "content": ev["payload"]["answer"]})
            self._conversations[session_id] = conv
        return conv

    async def handle(self, session_id: str, user_query: str, emit) -> None:
        """在工作副本中处理一轮，对取消实行会话上下文原子回滚。"""
        conversation = self._get_conversation(session_id)
        base_length = len(conversation)
        working = list(conversation)

        def commit() -> None:
            conversation.extend(working[base_length:])

        try:
            await self._handle_turn(session_id, user_query, emit, working)
        except asyncio.CancelledError:
            # working 尚未提交，共享上下文天然保持本轮开始前的状态。
            raise
        except Exception:
            # 非取消异常保持原有语义：此前已经追加的上下文仍然可见。
            commit()
            raise
        else:
            commit()

    async def _handle_turn(self, session_id: str, user_query: str, emit,
                           conversation: list[dict]) -> None:
        started = time.monotonic()

        async def record(event_type: str, payload: dict):
            h = self._audit.append(session_id, event_type, payload)
            await emit({"type": event_type, "session_id": session_id,
                        "hash": h, **payload})

        async def phase(name: str, **extra):
            # 阶段指示：纯 UI 事件（不入审计），让内部工作对用户可感
            await emit({"type": "phase", "session_id": session_id,
                        "phase": name, **extra})

        async def progress(stage: str, operation_id: str, update: dict,
                           **extra):
            """统一补全瞬时进度事件；progress 不进入审计链。"""
            payload = {
                "type": "progress",
                "session_id": session_id,
                "stage": stage,
                "operation_id": operation_id,
                "state": update["state"],
                "attempt": update.get("attempt", 1),
                "max_attempts": update.get("max_attempts", 1),
                "elapsed_ms": update.get("elapsed_ms", 0),
                "retry_in_ms": update.get("retry_in_ms", 0),
                **extra,
            }
            if update.get("error") is not None:
                payload["error"] = update["error"]
            await emit(payload)

        def elapsed_ms() -> int:
            return int((time.monotonic() - started) * 1000)

        async def fail_task(stage: str, operation_id: str,
                            error: PublicError, answer: str | None = None):
            error_payload = error.to_dict()
            await record("task_error", {
                "stage": stage,
                "operation_id": operation_id,
                "elapsed_ms": elapsed_ms(),
                "error": error_payload,
            })
            final_text = answer or (
                f"{error.message} 任务已中止。错误编号：{error.incident_id}"
            )
            await record("final_answer", {
                "answer": final_text,
                "aborted": True,
                "outcome": "failed",
                "elapsed_ms": elapsed_ms(),
            })

        await record("user_query", {"query": user_query})

        intent = screen_user_intent(user_query)
        if intent.decision == RuleDecision.DENY:
            await record("intent_filter", {"decision": intent.model_dump()})
            await record("final_answer", {
                "answer": f"请求已被安全意图校验器拒绝：{intent.reason}",
                "aborted": True,
                "outcome": "blocked",
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
            planning_operation = f"planning:{round_no}"

            async def on_delta(text: str, _round=round_no):
                await emit({"type": "assistant_delta",
                            "session_id": session_id,
                            "round": _round, "text": text})

            async def on_planning_progress(update: dict, _round=round_no,
                                           _op=planning_operation):
                await progress("planning", _op, update, round=_round)

            try:
                plan = await self._planner.next_actions(
                    conversation, on_delta=on_delta,
                    on_progress=on_planning_progress,
                )
            except LLMError as exc:
                await fail_task("planning", planning_operation, exc.error)
                return
            except PlanningError:
                error = public_error(
                    "planner_output_invalid",
                    "模型连续返回了无法处理的规划格式。",
                    retryable=False,
                )
                await fail_task("planning", planning_operation, error)
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
                                              "outcome": "completed",
                                              "elapsed_ms": elapsed_ms()})
                return

            observations = []
            for step, step_id in zip(plan.steps, step_ids):
                observations.append(await self._run_step(
                    user_query, env_summary, step, step_id,
                    record, phase, progress))

            conversation.append({"role": "assistant",
                                 "content": plan.model_dump_json()})
            conversation.append({
                "role": "user",
                "content": "各步骤执行结果：\n\n" + "\n\n".join(observations)
                           + "\n\n请基于以上结果继续规划，或给出最终结论。",
            })

        error = public_error(
            "iteration_limit_reached",
            f"迭代轮数达到上限（{self._settings.max_iterations}）。",
            retryable=False,
        )
        await fail_task(
            "planning", f"planning:{self._settings.max_iterations}", error,
            answer=(f"迭代轮数达到上限（{self._settings.max_iterations}），"
                    "任务中止。请缩小问题范围后重试。"),
        )

    async def _run_step(self, user_query: str, env_summary: str,
                        step: PlanStep, step_id: str, record, phase,
                        progress) -> str:
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
        review_operation = f"reviewing:{step_id}"

        async def on_review_progress(update: dict):
            await progress(
                "reviewing", review_operation, update,
                step_id=step_id, tool=step.tool,
            )

        review = await self._reviewer.review(
            user_query, env_summary, action_desc,
            on_progress=on_review_progress,
        )
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
                "timeout_seconds": self._settings.confirm_timeout,
            })
            try:
                approved, operator = await asyncio.wait_for(
                    fut, timeout=self._settings.confirm_timeout)
            except asyncio.TimeoutError:
                self.confirmations.resolve(confirm_id, False)  # 清理挂起项
                approved, operator = False, "(超时)"
            except asyncio.CancelledError:
                # wait_for 会一并取消 Future；仍需从 pending 映射中移除。
                self.confirmations.resolve(confirm_id, False)
                raise
            await record("confirm_result",
                         {"confirm_id": confirm_id, "step_id": step_id,
                          "approved": approved, "operator": operator,
                          "timed_out": operator == "(超时)"})
            if not approved:
                return f"步骤 {step.tool} 未获管理员批准（拒绝或超时），已跳过"

        # ④ 受限执行（经 MCP 插件进程）
        started = time.monotonic()
        execution_operation = f"executing:{step_id}"
        base_progress = {
            "attempt": 1,
            "max_attempts": 1,
            "elapsed_ms": 0,
            "retry_in_ms": 0,
        }
        await progress(
            "executing", execution_operation,
            {"state": "connecting", **base_progress},
            step_id=step_id, tool=step.tool,
        )
        ok = True
        error_payload = None
        try:
            output = await self._tools.call(server, tool, step.arguments)
        except ToolCallError as exc:
            ok = False
            output = str(exc)[:8000] or "工具未返回错误详情。"
            error = public_error(
                "tool_call_failed",
                "工具返回失败，未完成该步骤。",
                retryable=False,
            )
            error_payload = error.to_dict()
        except Exception:
            ok = False
            error = public_error(
                "tool_call_failed",
                "工具调用失败，未完成该步骤。",
                retryable=False,
            )
            error_payload = error.to_dict()
            output = error.message
        duration_ms = int((time.monotonic() - started) * 1000)
        # ⑤ 溯源
        await record("execution", {
            "step_id": step_id,
            "operation_id": execution_operation,
            "step": step.model_dump(),
            "duration_ms": duration_ms,
            "output": output[:8000],
            "ok": ok,
            "error": error_payload,
        })
        await progress(
            "executing", execution_operation,
            {
                "state": "completed" if ok else "failed",
                "attempt": 1,
                "max_attempts": 1,
                "elapsed_ms": duration_ms,
                "retry_in_ms": 0,
                **({"error": error_payload} if error_payload else {}),
            },
            step_id=step_id, tool=step.tool,
        )
        if not ok:
            return (f"步骤 {step.tool} 调用失败：{output}"
                    f"（错误编号：{error_payload['incident_id']}）")
        return f"步骤 {step.tool} 输出：\n{output[:4000]}"
