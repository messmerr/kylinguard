import pytest

from kylinguard.llm import LLMClient, LLMError


class _StatusError(RuntimeError):
    def __init__(self, status_code: int):
        super().__init__("原始服务商错误不应进入公开事件")
        self.status_code = status_code
        self.request_id = "req-test-1"


class _FakeCompletions:
    def __init__(self, fails: int, answer: str = "好的", status: int = 429):
        self.fails = fails
        self.answer = answer
        self.status = status
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fails:
            raise _StatusError(self.status)

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


async def test_401不重试且错误已清洗(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=3)
    fake = _FakeCompletions(fails=99, status=401)
    _patch(c, fake, monkeypatch)
    events = []

    async def progress(event):
        events.append(event)

    with pytest.raises(LLMError) as raised:
        await c.chat([{"role": "user", "content": "hi"}],
                     on_progress=progress)
    assert fake.calls == 1
    assert raised.value.error.code == "llm_auth_invalid"
    assert events[-1]["state"] == "failed"
    assert events[-1]["error"]["http_status"] == 401
    assert "原始服务商错误" not in str(events)


async def test_空key立即失败且不访问服务商(monkeypatch):
    c = LLMClient("https://x", "", "m", max_retries=3)
    fake = _FakeCompletions(fails=0)
    _patch(c, fake, monkeypatch)
    events = []

    async def progress(event):
        events.append(event)

    with pytest.raises(LLMError) as raised:
        await c.chat([{"role": "user", "content": "hi"}],
                     on_progress=progress)
    assert fake.calls == 0
    assert raised.value.error.code == "llm_config_missing"
    assert [event["state"] for event in events] == ["connecting", "failed"]


async def test_429重试进度可见(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=3)
    fake = _FakeCompletions(fails=1, status=429)
    _patch(c, fake, monkeypatch)
    events = []

    async def progress(event):
        events.append(event)

    assert await c.chat([{"role": "user", "content": "hi"}],
                        on_progress=progress) == "好的"
    retry = next(event for event in events if event["state"] == "retry_wait")
    assert retry["attempt"] == 1 and retry["max_attempts"] == 3
    assert retry["retry_in_ms"] == 1000
    assert retry["error"]["code"] == "llm_rate_limited"
    assert events[-1]["state"] == "completed"


@pytest.mark.parametrize(("status", "code"), [
    (408, "llm_timeout"),
    (409, "llm_conflict"),
])
async def test_408与409会重试且进度可见(monkeypatch, status, code):
    c = LLMClient("https://x", "k", "m", max_retries=3)
    fake = _FakeCompletions(fails=1, status=status)
    _patch(c, fake, monkeypatch)
    events = []

    async def progress(event):
        events.append(event)

    assert await c.chat([{"role": "user", "content": "hi"}],
                        on_progress=progress) == "好的"
    retry = next(event for event in events if event["state"] == "retry_wait")
    assert retry["error"]["code"] == code
    assert retry["error"]["retryable"] is True
    assert fake.calls == 2


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
        if isinstance(part, BaseException):
            raise part

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
            raise _StatusError(429)
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


async def test_流出token后中断不重试(monkeypatch):
    c = LLMClient("https://x", "k", "m", max_retries=3)
    fake = _FakeStreamCompletions(0, ["半截", _StatusError(500)])
    _patch(c, fake, monkeypatch)
    events = []

    async def progress(event):
        events.append(event)

    got = []
    with pytest.raises(LLMError) as raised:
        async for part in c.chat_stream(
                [{"role": "user", "content": "hi"}],
                on_progress=progress):
            got.append(part)
    assert got == ["半截"] and fake.calls == 1
    assert raised.value.partial is True
    assert raised.value.error.code == "llm_stream_interrupted"
    assert events[-1]["state"] == "failed"
    assert not any(event["state"] == "retry_wait" for event in events)
