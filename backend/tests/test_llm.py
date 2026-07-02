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
