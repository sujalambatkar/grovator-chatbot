"""Single wrapper around the Groq SDK: retries, timeouts, JSON mode, usage logging, fallback."""
import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

from app.config import Settings

log = logging.getLogger("grovator.llm")


@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    fallback_used: bool = False
    finish_reason: str | None = None  # "length" means the answer hit max_tokens

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


class LLMError(Exception):
    pass


class LLMClient(Protocol):
    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_mode: bool = False,
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ) -> LLMResult: ...

    async def answer(self, messages: list[dict[str, str]]) -> LLMResult: ...

    def answer_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[tuple[str, Any]]: ...


def _retryable(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    return status == 429 or (isinstance(status, int) and status >= 500) or name in (
        "APIConnectionError",
        "APITimeoutError",
        "RateLimitError",
        "InternalServerError",
    )


class GroqLLMClient:
    def __init__(self, settings: Settings, sdk_client: Any | None = None) -> None:
        self.s = settings
        if sdk_client is None:
            from groq import AsyncGroq

            sdk_client = AsyncGroq(api_key=settings.groq_api_key, timeout=settings.llm_timeout_s, max_retries=0)
        self._sdk = sdk_client

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        max_tokens: int,
        json_mode: bool = False,
        temperature: float = 0.2,
        reasoning_effort: str | None = None,
    ) -> LLMResult:
        kwargs: dict[str, Any] = dict(model=model, messages=messages, max_tokens=max_tokens, temperature=temperature)
        effort = reasoning_effort or self.s.reasoning_effort
        if effort and "gpt-oss" in model:  # param only valid for reasoning models
            kwargs["reasoning_effort"] = effort
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        last: Exception | None = None
        for attempt in range(self.s.llm_max_retries):
            t0 = time.perf_counter()
            try:
                resp = await self._sdk.chat.completions.create(**kwargs)
                usage = resp.usage
                result = LLMResult(
                    text=resp.choices[0].message.content or "",
                    model=model,
                    input_tokens=getattr(usage, "prompt_tokens", 0),
                    output_tokens=getattr(usage, "completion_tokens", 0),
                    latency_ms=int((time.perf_counter() - t0) * 1000),
                )
                log.info(
                    "llm_call model=%s in=%d out=%d ms=%d",
                    model, result.input_tokens, result.output_tokens, result.latency_ms,
                )
                return result
            except Exception as exc:  # noqa: BLE001 - classified below
                last = exc
                if not _retryable(exc) or attempt == self.s.llm_max_retries - 1:
                    break
                await asyncio.sleep(0.5 * 2**attempt)
        raise LLMError(f"LLM call failed for {model}: {type(last).__name__}") from last

    async def answer(self, messages: list[dict[str, str]]) -> LLMResult:
        """Large model; on failure fall back to the small model with a shorter answer."""
        try:
            return await self.chat(
                model=self.s.answer_model, messages=messages, max_tokens=self.s.answer_max_tokens
            )
        except LLMError:
            short = [*messages[:-1], {**messages[-1], "content": messages[-1]["content"] + "\n(Keep it under 80 words.)"}]
            res = await self.chat(model=self.s.router_model, messages=short, max_tokens=150)
            res.fallback_used = True
            return res

    async def answer_stream(self, messages: list[dict[str, str]]) -> AsyncIterator[tuple[str, Any]]:
        """Yields ("delta", text) ... then ("done", LLMResult). If the stream fails before any
        text was sent, falls back to the non-streaming path (big model, then small model)."""
        from app.rag.tokens import count_tokens

        kwargs: dict[str, Any] = dict(
            model=self.s.answer_model, messages=messages, max_tokens=self.s.answer_max_tokens,
            temperature=0.2, stream=True,
        )
        if self.s.reasoning_effort and "gpt-oss" in self.s.answer_model:
            kwargs["reasoning_effort"] = self.s.reasoning_effort
        parts: list[str] = []
        usage = None
        finish = None
        t0 = time.perf_counter()
        try:
            stream = await self._sdk.chat.completions.create(**kwargs)
            async for chunk in stream:
                usage = getattr(getattr(chunk, "x_groq", None), "usage", None) or getattr(chunk, "usage", None) or usage
                if chunk.choices and getattr(chunk.choices[0], "finish_reason", None):
                    finish = chunk.choices[0].finish_reason
                if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                    text = chunk.choices[0].delta.content
                    parts.append(text)
                    yield "delta", text
        except Exception as exc:  # noqa: BLE001
            if parts:
                raise LLMError(f"stream interrupted: {type(exc).__name__}") from exc
            res = await self.answer(messages)
            yield "delta", res.text
            yield "done", res
            return
        text = "".join(parts)
        yield "done", LLMResult(
            text=text, model=self.s.answer_model,
            input_tokens=getattr(usage, "prompt_tokens", None) or sum(count_tokens(m["content"]) for m in messages),
            output_tokens=getattr(usage, "completion_tokens", None) or count_tokens(text),
            latency_ms=int((time.perf_counter() - t0) * 1000), finish_reason=finish,
        )
