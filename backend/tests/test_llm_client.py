from types import SimpleNamespace

import pytest

from app.config import Settings
from app.llm.llm_client import GroqLLMClient, LLMError


class RateLimit(Exception):
    status_code = 429


class FakeSDK:
    def __init__(self, behaviours):
        self.behaviours, self.calls = list(behaviours), []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        b = self.behaviours.pop(0)
        if isinstance(b, Exception):
            raise b
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=b))],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )


def client(behaviours, **kw):
    sdk = FakeSDK(behaviours)
    return GroqLLMClient(Settings(llm_max_retries=2, **kw), sdk_client=sdk), sdk


async def test_retries_then_succeeds():
    c, sdk = client([RateLimit(), "ok"])
    res = await c.chat(model="m", messages=[], max_tokens=10)
    assert res.text == "ok" and res.input_tokens == 10 and res.output_tokens == 5 and len(sdk.calls) == 2


async def test_non_retryable_fails_fast():
    c, sdk = client([ValueError("bad")])
    with pytest.raises(LLMError):
        await c.chat(model="m", messages=[], max_tokens=10)
    assert len(sdk.calls) == 1


async def test_json_mode_sets_response_format():
    c, sdk = client(['{"a":1}'])
    res = await c.chat(model="m", messages=[], max_tokens=10, json_mode=True)
    assert sdk.calls[0]["response_format"] == {"type": "json_object"} and res.json() == {"a": 1}


async def test_answer_falls_back_to_small_model():
    c, sdk = client([RateLimit(), RateLimit(), "short"], answer_model="big", router_model="small")
    res = await c.answer([{"role": "system", "content": "s"}, {"role": "user", "content": "q"}])
    assert res.fallback_used and res.model == "small" and sdk.calls[-1]["max_tokens"] == 150
    assert "under 80 words" in sdk.calls[-1]["messages"][-1]["content"]


async def test_reasoning_effort_only_for_reasoning_models():
    c, sdk = client(["a", "b"])
    await c.chat(model="openai/gpt-oss-20b", messages=[], max_tokens=10)
    await c.chat(model="some-other-model", messages=[], max_tokens=10)
    assert sdk.calls[0]["reasoning_effort"] == "low" and "reasoning_effort" not in sdk.calls[1]


class StreamSDK:
    """Fake Groq stream. fail_before / fail_after control where the stream dies."""
    def __init__(self, deltas, fail_before=False, fail_after=False, usage=True):
        self.deltas, self.fail_before, self.fail_after, self.usage = deltas, fail_before, fail_after, usage
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        if kw.get("stream") is not True:  # fallback (non-stream) call
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="fallback text"))],
                                   usage=SimpleNamespace(prompt_tokens=7, completion_tokens=2))
        if self.fail_before:
            raise RateLimit()

        async def gen():
            for i, d in enumerate(self.deltas):
                yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=d))], x_groq=None, usage=None)
                if self.fail_after and i == 0:
                    raise RateLimit()
            last = SimpleNamespace(prompt_tokens=11, completion_tokens=4) if self.usage else None
            yield SimpleNamespace(choices=[], x_groq=SimpleNamespace(usage=last), usage=None)
        return gen()


async def collect(c):
    return [x async for x in c.answer_stream([{"role": "user", "content": "q"}])]


async def test_stream_yields_deltas_then_usage():
    c = GroqLLMClient(Settings(llm_max_retries=1), sdk_client=StreamSDK(["Hel", "lo"]))
    out = await collect(c)
    assert [v for k, v in out if k == "delta"] == ["Hel", "lo"]
    res = out[-1][1]
    assert out[-1][0] == "done" and res.text == "Hello" and res.input_tokens == 11 and res.output_tokens == 4


async def test_stream_estimates_usage_when_missing():
    c = GroqLLMClient(Settings(llm_max_retries=1), sdk_client=StreamSDK(["Hello"], usage=False))
    res = (await collect(c))[-1][1]
    assert res.input_tokens > 0 and res.output_tokens > 0


async def test_stream_failure_before_first_token_falls_back():
    c = GroqLLMClient(Settings(llm_max_retries=1), sdk_client=StreamSDK([], fail_before=True))
    out = await collect(c)
    assert out[0] == ("delta", "fallback text") and out[-1][1].text == "fallback text"


async def test_stream_failure_midway_raises():
    c = GroqLLMClient(Settings(llm_max_retries=1), sdk_client=StreamSDK(["a", "b"], fail_after=True))
    with pytest.raises(LLMError):
        await collect(c)
