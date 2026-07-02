import pytest

from kylinguard.config import Settings
from kylinguard.llm import LLMClient, LLMError, build_clients


class _FakeCompletions:
    def __init__(self, fails: int, answer: str = "好的"):
        self.fails = fails
        self.answer = answer
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("模拟限流")

        answer = self.answer

        class _Msg:  # 模拟 openai 响应结构
            content = answer

        class _Choice:
            message = _Msg()

        class _Resp:
            choices = [_Choice()]

        return _Resp()


def _patch(client: LLMClient, fake: _FakeCompletions, monkeypatch):
    monkeypatch.setattr(client._client.chat, "completions", fake)
    # 重试不真等
    import kylinguard.llm as llm_mod

    async def _nosleep(_):
        pass

    monkeypatch.setattr(llm_mod.asyncio, "sleep", _nosleep)


async def test_成功返回内容(monkeypatch):
    c = LLMClient("https://x", "k", "m")
    _patch(c, _FakeCompletions(fails=0, answer="内容"), monkeypatch)
    assert await c.chat([{"role": "user", "content": "hi"}]) == "内容"


async def test_失败两次后重试成功(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=3)
    fake = _FakeCompletions(fails=2)
    _patch(c, fake, monkeypatch)
    assert await c.chat([{"role": "user", "content": "hi"}]) == "好的"
    assert fake.calls == 3


async def test_重试耗尽抛LLMError(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=2)
    _patch(c, _FakeCompletions(fails=99), monkeypatch)
    with pytest.raises(LLMError):
        await c.chat([{"role": "user", "content": "hi"}])


def test_双实例配置():
    s = Settings(_env_file=None, llm_api_key="k",
                 planner_model="deepseek-v4-pro", reviewer_model="qwen-max")
    planner, reviewer = build_clients(s)
    assert planner.model == "deepseek-v4-pro"
    assert reviewer.model == "qwen-max"


class _FakeStream:
    """模拟 openai 流式响应的 async 迭代器。"""

    def __init__(self, parts):
        self.parts = list(parts)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.parts:
            raise StopAsyncIteration

        part = self.parts.pop(0)

        class _Delta:
            content = part

        class _Choice:
            delta = _Delta()

        class _Chunk:
            choices = [_Choice()]

        return _Chunk()


class _FakeStreamCompletions:
    def __init__(self, fails: int, parts):
        self.fails = fails
        self.parts = parts
        self.calls = 0

    async def create(self, **kwargs):
        assert kwargs.get("stream") is True
        self.calls += 1
        if self.calls <= self.fails:
            raise RuntimeError("模拟限流")
        return _FakeStream(self.parts)


async def test_流式返回增量(monkeypatch):
    c = LLMClient("https://x", "k", "m")
    _patch(c, _FakeStreamCompletions(0, ["你", "好", None, "呀"]), monkeypatch)
    got = [d async for d in c.chat_stream([{"role": "user", "content": "hi"}])]
    assert got == ["你", "好", "呀"]  # None 增量被跳过


async def test_流式建立连接阶段重试(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=3)
    fake = _FakeStreamCompletions(2, ["ok"])
    _patch(c, fake, monkeypatch)
    got = [d async for d in c.chat_stream([{"role": "user", "content": "hi"}])]
    assert got == ["ok"] and fake.calls == 3


async def test_流式重试耗尽抛LLMError(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=2)
    _patch(c, _FakeStreamCompletions(99, ["x"]), monkeypatch)
    with pytest.raises(LLMError):
        async for _ in c.chat_stream([{"role": "user", "content": "hi"}]):
            pass
