"""LLM 网关：OpenAI 兼容客户端封装，指数退避重试。

DeepSeek / Qwen 等任意 OpenAI 兼容端点均可经配置切换（应对实机演示网络风险）。
"""
import asyncio

from openai import AsyncOpenAI

from kylinguard.config import Settings


class LLMError(RuntimeError):
    """LLM 调用最终失败（重试耗尽）。"""


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 max_retries: int = 3):
        # 空密钥用占位符：允许服务无密钥启动（协议级验证），真实调用时才报错
        self._client = AsyncOpenAI(base_url=base_url, api_key=api_key or "unset")
        self.model = model
        self.max_retries = max_retries

    async def chat(self, messages: list[dict], temperature: float = 0.2) -> str:
        delay = 1.0
        last_err: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.chat.completions.create(
                    model=self.model, messages=messages, temperature=temperature
                )
                return resp.choices[0].message.content or ""
            except Exception as e:  # openai 网络/限流/超时异常族
                last_err = e
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
        raise LLMError(f"LLM 调用失败（已重试 {self.max_retries} 次）：{last_err}")


def build_clients(settings: Settings) -> tuple[LLMClient, LLMClient]:
    """规划与审查双实例：系统提示词彼此独立，模型可分别配置。"""
    planner = LLMClient(settings.llm_base_url, settings.llm_api_key,
                        settings.planner_model, settings.llm_max_retries)
    reviewer = LLMClient(settings.llm_base_url, settings.llm_api_key,
                         settings.reviewer_model, settings.llm_max_retries)
    return planner, reviewer
