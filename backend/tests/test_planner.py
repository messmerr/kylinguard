import json

import pytest

from kylinguard.planner import Planner, PlanningError, extract_json

STEPS_JSON = json.dumps({
    "steps": [{"tool": "sysinfo.top_processes", "arguments": {"limit": 5},
               "purpose": "找出 CPU 大户", "risk": "low"}],
}, ensure_ascii=False)

GOOD = f"先看看哪些进程占用最高。\n\n```json\n{STEPS_JSON}\n```"
DONE = "系统负载**正常**，无需处理。\n\n```json\n{\"steps\": []}\n```"


class FakeStreamLLM:
    """按脚本回复的流式伪 LLM：每次取一条回复，按 3 字符切块流出。"""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.received: list[list[dict]] = []

    async def chat_stream(self, messages, temperature=0.2, on_progress=None):
        self.received.append([dict(m) for m in messages])
        text = self.replies.pop(0)
        for i in range(0, len(text), 3):
            yield text[i:i + 3]


async def _run(planner, conversation, collect=True):
    deltas = []

    async def on_delta(t):
        deltas.append(t)

    out = await planner.next_actions(
        conversation, on_delta=on_delta if collect else None)
    return out, "".join(deltas)


def test_extract_json_仍可用():  # reviewer 依赖
    assert extract_json('{"a": 1}')["a"] == 1
    with pytest.raises(ValueError):
        extract_json("没有 JSON")


async def test_双段解析_文本加json块():
    out, streamed = await _run(Planner(FakeStreamLLM([GOOD])),
                               [{"role": "user", "content": "系统卡"}])
    assert out.steps[0].tool == "sysinfo.top_processes"
    assert out.thought == "先看看哪些进程占用最高。"
    assert out.final_answer is None


async def test_流式增量不含json块():
    out, streamed = await _run(Planner(FakeStreamLLM([GOOD])),
                               [{"role": "user", "content": "系统卡"}])
    assert streamed.rstrip() == "先看看哪些进程占用最高。"
    assert "```" not in streamed and "steps" not in streamed


async def test_steps为空时文本即最终答案():
    out, streamed = await _run(Planner(FakeStreamLLM([DONE])),
                               [{"role": "user", "content": "看看负载"}])
    assert out.steps == []
    assert out.final_answer == "系统负载**正常**，无需处理。"


async def test_无json块宽松收敛为最终答案():
    out, streamed = await _run(
        Planner(FakeStreamLLM(["一切正常，没什么要做的。"])),
        [{"role": "user", "content": "看看"}])
    assert out.steps == []
    assert out.final_answer == "一切正常，没什么要做的。"
    assert streamed.rstrip() == "一切正常，没什么要做的。"


async def test_普通代码块正常外发():
    reply = ("可以用命令查看：\n```bash\nfree -m\n```\n我来执行。\n\n"
             f"```json\n{STEPS_JSON}\n```")
    out, streamed = await _run(Planner(FakeStreamLLM([reply])),
                               [{"role": "user", "content": "内存"}])
    assert "free -m" in streamed  # bash 示例块照常流出
    assert '"steps"' not in streamed
    assert out.steps


async def test_json块损坏带反馈重试():
    bad = "分析中。\n```json\n{steps: 不是合法json}\n```"
    fake = FakeStreamLLM([bad, GOOD])
    out, streamed = await _run(Planner(fake, max_json_retries=3),
                               [{"role": "user", "content": "系统卡"}])
    assert out.steps
    second = fake.received[1]
    assert second[-2]["content"].startswith("分析中。")  # 坏输出带回去了
    assert "json" in second[-1]["content"]


async def test_重试耗尽拒绝执行():
    bad = "x\n```json\n{坏}\n```"
    with pytest.raises(PlanningError):
        await _run(Planner(FakeStreamLLM([bad, bad]), max_json_retries=2),
                   [{"role": "user", "content": "x"}])


async def test_不污染传入的会话列表():
    conv = [{"role": "user", "content": "x"}]
    bad = "x\n```json\n{坏}\n```"
    await _run(Planner(FakeStreamLLM([bad, GOOD]), max_json_retries=2), conv)
    assert len(conv) == 1
