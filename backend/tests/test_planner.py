import json

import pytest

from kylinguard.planner import Planner, PlanningError, extract_json

GOOD = json.dumps({
    "thought": "先查负载",
    "steps": [{"tool": "sysinfo.top_processes", "arguments": {"limit": 5},
               "purpose": "找出 CPU 大户", "risk": "low"}],
    "final_answer": None,
}, ensure_ascii=False)


class FakeLLM:
    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.received: list[list[dict]] = []

    async def chat(self, messages, temperature=0.2):
        self.received.append([dict(m) for m in messages])
        return self.replies.pop(0)


def test_extract_json_剥围栏与前后缀():
    assert extract_json(f"好的，计划如下：\n```json\n{GOOD}\n```")["thought"] == "先查负载"
    assert extract_json(GOOD)["steps"][0]["tool"] == "sysinfo.top_processes"
    with pytest.raises(ValueError):
        extract_json("这里没有 JSON")


async def test_一次成功():
    p = Planner(FakeLLM([GOOD]))
    out = await p.next_actions([{"role": "user", "content": "系统卡"}])
    assert out.steps[0].tool == "sysinfo.top_processes"


async def test_解析失败带错误反馈重试():
    fake = FakeLLM(["我觉得应该先看看进程哦", GOOD])
    p = Planner(fake, max_json_retries=3)
    out = await p.next_actions([{"role": "user", "content": "系统卡"}])
    assert out.steps
    # 第二次请求里带上了上次的坏输出和纠错提示
    second = fake.received[1]
    assert second[-2]["content"] == "我觉得应该先看看进程哦"
    assert "JSON" in second[-1]["content"]


async def test_重试耗尽拒绝执行():
    p = Planner(FakeLLM(["坏1", "坏2"]), max_json_retries=2)
    with pytest.raises(PlanningError):
        await p.next_actions([{"role": "user", "content": "x"}])


async def test_不污染传入的会话列表():
    conv = [{"role": "user", "content": "x"}]
    await Planner(FakeLLM(["坏的", GOOD]), max_json_retries=2).next_actions(conv)
    assert len(conv) == 1
