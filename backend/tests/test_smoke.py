import json

from kylinguard.audit import AuditLog
from kylinguard.config import Settings
from kylinguard.mcp_client import ToolManager
from kylinguard.pipeline import Confirmations, Pipeline
from kylinguard.planner import Planner
from kylinguard.reviewer import Reviewer


class ScriptedLLM:
    """按脚本回复的伪 LLM：第一轮出计划，第二轮收尾。"""

    def __init__(self, replies):
        self.replies = list(replies)

    async def chat(self, messages, temperature=0.2):
        return self.replies.pop(0)

    async def chat_stream(self, messages, temperature=0.2):
        text = self.replies.pop(0)
        for i in range(0, len(text), 5):
            yield text[i:i + 5]


_STEPS = json.dumps({
    "steps": [{"tool": "sysinfo.disk_usage", "arguments": {},
               "purpose": "查看磁盘使用率", "risk": "low"}],
}, ensure_ascii=False)
PLAN = f"查一下磁盘。\n```json\n{_STEPS}\n```"
DONE = "磁盘情况见上，无异常。\n```json\n{\"steps\": []}\n```"
REVIEW_OK = json.dumps({"safe": True, "matches_intent": True,
                        "risk": "low", "reason": "只读查询"}, ensure_ascii=False)


async def test_全链路冒烟_真实MCP子进程(tmp_path):
    audit = AuditLog(str(tmp_path / "smoke.db"))
    tools = ToolManager()
    await tools.start()
    try:
        pipeline = Pipeline(
            settings=Settings(_env_file=None),
            audit=audit,
            tools=tools,
            planner=Planner(ScriptedLLM([PLAN, DONE])),
            reviewer=Reviewer(ScriptedLLM([REVIEW_OK])),
            confirmations=Confirmations(),
        )
        events = []

        async def emit(e):
            events.append(e)

        await pipeline.handle("smoke", "帮我看下磁盘", emit)
        chain_ok = audit.verify_chain("smoke")
    finally:
        await tools.stop()
        audit.close()

    types = [e["type"] for e in events]
    assert types[-1] == "final_answer"
    assert "execution" in types  # 真实经 MCP 调到了插件
    assert chain_ok is True
