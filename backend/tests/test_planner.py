import json

import pytest

from kylinguard.planner import (
    Planner, PlanningError, build_system_prompt, extract_json,
)

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


def test_系统提示明确参数契约与按意图选择回答格式():
    prompt = build_system_prompt(
        '- files.write_file [risk=high]: 写文件\n'
        '  参数: path: string (必填, 不可为 null)')

    assert "补齐全部必填字段" in prompt
    assert "严格遵守 JSON 类型、可选值、格式、范围及其他约束" in prompt
    assert "可选参数不需要时必须完全省略" in prompt
    assert "只有清单明确标注“可为 null”时才能传 null" in prompt
    assert "是否使用过工具不决定回答格式" in prompt
    assert "创作、普通文件操作、信息查询、解释" in prompt
    assert "只有当用户要求的是**故障诊断或运维处置**时" in prompt
    assert "系统快照只是背景证据" in prompt
    assert "不要在闲聊、创作或无关回答中主动汇报快照内容" in prompt
    assert '"tool": "sysinfo.disk_usage"' in prompt
    assert "服务器.工具名" not in prompt
    assert "必须逐字复制“可用工具清单”" in prompt
    assert "禁止添加“服务器”" in prompt
    assert "code=unknown_tool" in prompt


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


async def test_隐藏工具决策只上报节流后的安全生成进度():
    secret_path = "/tmp/不应出现在进度里的文件名.html"
    secret_content = "private-token-不应泄露\n" + "页面正文" * 900
    decision = json.dumps({
        "steps": [{
            "tool": "files.write_file",
            "arguments": {"path": secret_path, "content": secret_content},
            "purpose": "创建页面",
            "risk": "medium",
        }],
    }, ensure_ascii=False)
    reply = f"我来创建页面。\n```json\n{decision}\n```"
    progress = []
    deltas = []

    async def on_progress(update):
        progress.append(update)

    async def on_delta(text):
        deltas.append(text)

    out = await Planner(FakeStreamLLM([reply])).next_actions(
        [{"role": "user", "content": "创建页面"}],
        on_delta=on_delta,
        on_progress=on_progress,
    )

    decision_progress = [
        event for event in progress
        if event["state"] == "constructing_tool_call"
    ]
    assert out.steps[0].arguments["content"] == secret_content
    assert "".join(deltas).rstrip() == "我来创建页面。"
    assert {event["activity"] for event in decision_progress} >= {
        "constructing_tool_call",
        "preparing_file_path",
        "generating_file_content",
    }
    assert decision_progress[-1]["generated_chars"] > 0
    assert decision_progress[-1]["generated_bytes"] >= (
        decision_progress[-1]["generated_chars"]
    )
    # 三字符一个 chunk，但事件数量应远低于 chunk 数，避免 SSE 风暴。
    assert len(decision_progress) < len(reply) // 100
    public_progress = json.dumps(decision_progress, ensure_ascii=False)
    assert secret_path not in public_progress
    assert secret_content not in public_progress
    assert "private-token" not in public_progress


async def test_隐藏决策进度不会出现在模型完成事件之后():
    class OrderedFakeLLM(FakeStreamLLM):
        async def chat_stream(self, messages, temperature=0.2,
                              on_progress=None):
            self.received.append([dict(m) for m in messages])
            await on_progress({"state": "streaming"})
            text = self.replies.pop(0)
            for i in range(0, len(text), 3):
                yield text[i:i + 3]
            await on_progress({"state": "completed"})

    events = []

    async def on_progress(update):
        events.append(update)

    await Planner(OrderedFakeLLM([GOOD])).next_actions(
        [{"role": "user", "content": "系统卡"}],
        on_progress=on_progress,
    )

    completed = next(i for i, event in enumerate(events)
                     if event["state"] == "completed")
    constructing = [i for i, event in enumerate(events)
                    if event["state"] == "constructing_tool_call"]
    assert constructing
    assert max(constructing) < completed


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
