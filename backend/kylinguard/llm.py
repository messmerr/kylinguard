"""LLM 网关：OpenAI 兼容客户端、可观测重试与安全错误映射。

SDK 内置重试被关闭，所有重试均由本模块统一控制。这样调用方可以准确
获知每次连接、退避和失败，同时避免 SDK 重试与业务重试叠加。
"""
from __future__ import annotations

import asyncio
import inspect
import re
import time
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

ProgressCallback = Callable[[dict], Awaitable[None]]

_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")
_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 422}


@dataclass(frozen=True)
class PublicError:
    """允许进入 SSE、审计与普通日志的错误字段白名单。"""

    code: str
    message: str
    retryable: bool
    http_status: int | None = None
    request_id: str | None = None
    incident_id: str = ""

    def to_dict(self) -> dict:
        payload = {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "incident_id": self.incident_id or _incident_id(),
        }
        if self.http_status is not None:
            payload["http_status"] = self.http_status
        if self.request_id is not None:
            payload["request_id"] = self.request_id
        return payload


class LLMError(RuntimeError):
    """带安全公开信息的 LLM 终态错误。"""

    def __init__(self, error: PublicError, *, attempts: int,
                 partial: bool = False):
        super().__init__(error.message)
        self.error = error
        self.attempts = attempts
        self.partial = partial


class _ProgressCallbackError(RuntimeError):
    """防止进度消费者异常被误判为模型服务异常并触发重试。"""


def _incident_id() -> str:
    return f"err-{uuid.uuid4().hex[:12]}"


def public_error(code: str, message: str, *, retryable: bool,
                 http_status: int | None = None,
                 request_id: str | None = None,
                 incident_id: str | None = None) -> PublicError:
    """构造只含允许字段的公开错误。"""
    return PublicError(
        code=code,
        message=message,
        retryable=retryable,
        http_status=http_status,
        request_id=_sanitize_request_id(request_id),
        incident_id=incident_id or _incident_id(),
    )


def public_error_from_exception(exc: BaseException) -> PublicError:
    """将异常映射为稳定错误码；绝不复制原始异常文本或响应体。"""
    if isinstance(exc, LLMError):
        return exc.error

    status = _status_code(exc)
    request_id = _request_id(exc)
    common = {"http_status": status, "request_id": request_id}

    if isinstance(exc, APITimeoutError):
        return public_error("llm_timeout", "模型服务响应超时。",
                            retryable=True, **common)
    if isinstance(exc, APIConnectionError):
        return public_error("llm_unreachable", "无法连接模型服务。",
                            retryable=True, **common)
    if status == 400 or status == 422:
        return public_error("llm_request_invalid", "模型服务拒绝了当前请求。",
                            retryable=False, **common)
    if status == 401:
        return public_error("llm_auth_invalid",
                            "模型服务未接受当前凭据，请检查 API Key。",
                            retryable=False, **common)
    if status == 403:
        return public_error("llm_forbidden", "当前凭据无权调用模型服务。",
                            retryable=False, **common)
    if status == 404:
        return public_error("llm_model_not_found",
                            "模型或模型服务地址不存在，请检查配置。",
                            retryable=False, **common)
    if status == 408:
        return public_error("llm_timeout", "模型服务响应超时。",
                            retryable=True, **common)
    if status == 409:
        return public_error("llm_conflict", "模型服务暂时无法处理当前请求。",
                            retryable=True, **common)
    if status == 429:
        return public_error("llm_rate_limited", "模型服务繁忙，请稍后重试。",
                            retryable=True, **common)
    if status is not None and status >= 500:
        return public_error("llm_provider_unavailable", "模型服务暂时不可用。",
                            retryable=True, **common)
    if status in _NON_RETRYABLE_STATUS:
        return public_error("llm_request_failed", "模型服务拒绝了当前请求。",
                            retryable=False, **common)
    return public_error("llm_request_failed", "模型服务调用失败。",
                        retryable=False, **common)


def internal_error() -> PublicError:
    """API 未知异常的固定公开表示。"""
    return public_error("internal_error", "任务因内部错误中止。",
                        retryable=False)


def _status_code(exc: BaseException) -> int | None:
    value = getattr(exc, "status_code", None)
    if value is None:
        response = getattr(exc, "response", None)
        value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _request_id(exc: BaseException) -> str | None:
    value = getattr(exc, "request_id", None)
    if value is None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", None)
        if headers is not None:
            value = headers.get("x-request-id") or headers.get("request-id")
    return _sanitize_request_id(value)


def _sanitize_request_id(value) -> str | None:
    if not isinstance(value, str) or not _SAFE_REQUEST_ID.fullmatch(value):
        return None
    return value


def _retry_delay(exc: BaseException, fallback: float) -> float:
    """尊重有界 Retry-After；无有效值时使用指数退避。"""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    raw = headers.get("retry-after") if headers is not None else None
    try:
        return min(60.0, max(0.0, float(raw)))
    except (TypeError, ValueError):
        return fallback


async def _emit_progress(callback: ProgressCallback | None, state: str, *,
                         attempt: int, max_attempts: int, started: float,
                         retry_in_ms: int = 0,
                         error: PublicError | None = None) -> None:
    if callback is None:
        return
    payload = {
        "state": state,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "elapsed_ms": int((time.monotonic() - started) * 1000),
        "retry_in_ms": retry_in_ms,
    }
    if error is not None:
        payload["error"] = error.to_dict()
    try:
        await callback(payload)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _ProgressCallbackError() from exc


async def _close_stream(stream) -> None:
    close = getattr(stream, "close", None)
    if close is None:
        return
    result = close()
    if inspect.isawaitable(result):
        await result


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 max_retries: int = 3, timeout: float = 60.0, *,
                 adapter: str = "openai_compatible",
                 reasoning_effort: str = "auto",
                 supports_temperature: bool = True):
        self._configured = bool(api_key.strip())
        # 空密钥仍允许服务启动；第一次真实调用会返回结构化配置错误。
        # 禁止 30x 自动转发请求。跨 origin 时即使 Authorization 被移除，
        # 对话、系统快照和工具观察正文仍不应被重定向到未知主机。
        self._http_client = httpx.AsyncClient(follow_redirects=False)
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "unset",
            max_retries=0,
            timeout=timeout,
            http_client=self._http_client,
        )
        self.model = model
        self.adapter = adapter
        self.reasoning_effort = reasoning_effort
        self.supports_temperature = supports_temperature
        # 为兼容现有配置名，max_retries 表示包含首次请求在内的最大尝试数。
        self.max_attempts = max(1, max_retries)
        self.max_retries = self.max_attempts

    async def close(self) -> None:
        await self._client.close()

    def _completion_options(self, messages: list[dict], temperature: float,
                            *, stream: bool = False) -> dict:
        """按模型声明的能力和提供商协议构造请求，不根据模型名猜参数。"""
        options: dict = {"model": self.model, "messages": messages}
        if stream:
            options["stream"] = True
        if self.supports_temperature:
            options["temperature"] = temperature

        effort = self.reasoning_effort
        if effort == "auto":
            return options
        if self.adapter == "deepseek":
            if effort == "none":
                options["extra_body"] = {"thinking": {"type": "disabled"}}
            else:
                options["reasoning_effort"] = (
                    "max" if effort in {"xhigh", "max"} else effort)
                options["extra_body"] = {"thinking": {"type": "enabled"}}
            return options
        if self.adapter == "dashscope":
            if effort == "none":
                options["extra_body"] = {"enable_thinking": False}
            else:
                budgets = {
                    "minimal": 1024,
                    "low": 2048,
                    "medium": 8192,
                    "high": 24576,
                    "xhigh": 65536,
                    "max": 65536,
                }
                extra = {"enable_thinking": True}
                if effort in budgets:
                    extra["thinking_budget"] = budgets[effort]
                options["extra_body"] = extra
            return options
        # OpenAI 与显式选择兼容协议的提供商均使用标准字段；是否可选由
        # 模型 supported_efforts 能力列表在配置层提前校验。
        options["reasoning_effort"] = effort
        return options

    def _missing_key_error(self) -> PublicError | None:
        if self._configured:
            return None
        return public_error("llm_config_missing", "尚未配置模型服务 API Key。",
                            retryable=False)

    async def chat(self, messages: list[dict], temperature: float = 0.2,
                   on_progress: ProgressCallback | None = None) -> str:
        started = time.monotonic()
        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            await _emit_progress(on_progress, "connecting", attempt=attempt,
                                 max_attempts=self.max_attempts,
                                 started=started)
            missing = self._missing_key_error()
            if missing is not None:
                await _emit_progress(on_progress, "failed", attempt=attempt,
                                     max_attempts=self.max_attempts,
                                     started=started, error=missing)
                raise LLMError(missing, attempts=attempt)
            try:
                resp = await self._client.chat.completions.create(
                    **self._completion_options(messages, temperature)
                )
            except _ProgressCallbackError as exc:
                raise exc.__cause__ from exc
            except Exception as exc:
                error = public_error_from_exception(exc)
                if error.retryable and attempt < self.max_attempts:
                    wait = _retry_delay(exc, delay)
                    await _emit_progress(
                        on_progress, "retry_wait", attempt=attempt,
                        max_attempts=self.max_attempts, started=started,
                        retry_in_ms=int(wait * 1000), error=error,
                    )
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                await _emit_progress(on_progress, "failed", attempt=attempt,
                                     max_attempts=self.max_attempts,
                                     started=started, error=error)
                raise LLMError(error, attempts=attempt) from exc
            await _emit_progress(on_progress, "completed", attempt=attempt,
                                 max_attempts=self.max_attempts,
                                 started=started)
            return resp.choices[0].message.content or ""
        raise AssertionError("unreachable")

    async def chat_stream(self, messages: list[dict], temperature: float = 0.2,
                          on_progress: ProgressCallback | None = None):
        """逐个产出文本增量；只有尚未产出 token 的失败可以重试。"""
        started = time.monotonic()
        delay = 1.0
        for attempt in range(1, self.max_attempts + 1):
            await _emit_progress(on_progress, "connecting", attempt=attempt,
                                 max_attempts=self.max_attempts,
                                 started=started)
            missing = self._missing_key_error()
            if missing is not None:
                await _emit_progress(on_progress, "failed", attempt=attempt,
                                     max_attempts=self.max_attempts,
                                     started=started, error=missing)
                raise LLMError(missing, attempts=attempt)

            stream = None
            emitted_token = False
            streaming_notified = False
            try:
                stream = await self._client.chat.completions.create(
                    **self._completion_options(
                        messages, temperature, stream=True)
                )
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta.content
                    if not delta:
                        continue
                    if not streaming_notified:
                        await _emit_progress(
                            on_progress, "streaming", attempt=attempt,
                            max_attempts=self.max_attempts, started=started,
                        )
                        streaming_notified = True
                    emitted_token = True
                    yield delta
            except _ProgressCallbackError as exc:
                raise exc.__cause__ from exc
            except Exception as exc:
                error = public_error_from_exception(exc)
                if emitted_token:
                    error = public_error(
                        "llm_stream_interrupted",
                        "模型响应在传输过程中中断。",
                        retryable=False,
                        http_status=error.http_status,
                        request_id=error.request_id,
                        incident_id=error.incident_id,
                    )
                if error.retryable and attempt < self.max_attempts:
                    wait = _retry_delay(exc, delay)
                    await _emit_progress(
                        on_progress, "retry_wait", attempt=attempt,
                        max_attempts=self.max_attempts, started=started,
                        retry_in_ms=int(wait * 1000), error=error,
                    )
                    await asyncio.sleep(wait)
                    delay *= 2
                    continue
                await _emit_progress(on_progress, "failed", attempt=attempt,
                                     max_attempts=self.max_attempts,
                                     started=started, error=error)
                raise LLMError(error, attempts=attempt,
                               partial=emitted_token) from exc
            finally:
                if stream is not None:
                    await _close_stream(stream)

            await _emit_progress(on_progress, "completed", attempt=attempt,
                                 max_attempts=self.max_attempts,
                                 started=started)
            return
        raise AssertionError("unreachable")
