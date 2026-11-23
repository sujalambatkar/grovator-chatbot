"""Gemini client: request shape, usage mapping, retries, errors that never leak the key, provider selection."""
import json
import logging

import httpx
import pytest

from app.config import Settings, production_problems
from app.llm.llm_client import GeminiLLMClient, GroqLLMClient, LLMError, make_llm

KEY = "AIzaSy-test-key-should-never-appear-anywhere"


def settings(**kw):
    return Settings(llm_provider="gemini", gemini_api_key=KEY, llm_max_retries=3, usage_log_dir="", **kw)


def client(handler, **kw):
    return GeminiLLMClient(settings(**kw), http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def ok(text="Hello", prompt=11, out=5, thoughts=0, finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": finish}],
                                     "usageMetadata": {"promptTokenCount": prompt, "candidatesTokenCount": out, "thoughtsTokenCount": thoughts}})


@pytest.mark.asyncio
async def test_request_shape_and_result_mapping():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["key"], seen["body"] = str(req.url), req.headers.get("x-goog-api-key"), json.loads(req.content)
        return ok("Wire VCC to 5V.", prompt=120, out=30, thoughts=20)

    c = client(handler)
    msgs = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}, {"role": "user", "content": "q2"}]
    r = await c.chat(model="gemini-2.5-flash-lite", messages=msgs, max_tokens=300, temperature=0.1)
    assert seen["url"].endswith("/models/gemini-2.5-flash-lite:generateContent") and KEY not in seen["url"]      # key is a header, never in the URL
    assert seen["key"] == KEY
    b = seen["body"]
    assert b["systemInstruction"]["parts"][0]["text"] == "SYS"
    assert [c_["role"] for c_ in b["contents"]] == ["user", "model", "user"]                                    # assistant -> model
    assert b["generationConfig"]["maxOutputTokens"] == 300 and "responseMimeType" not in b["generationConfig"]
    assert "thinkingConfig" not in b["generationConfig"]                                                         # default: model decides
    assert (r.text, r.input_tokens, r.output_tokens) == ("Wire VCC to 5V.", 120, 50)                              # thinking tokens are output tokens


@pytest.mark.asyncio
async def test_json_mode_thinking_budget_and_truncation_flag():
    seen = {}

    def handler(req):
        seen["gen"] = json.loads(req.content)["generationConfig"]
        return ok('{"intent":"platform_faq"}', finish="MAX_TOKENS")

    r = await client(handler, gemini_thinking_budget=0).chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=50, json_mode=True)
    assert seen["gen"]["responseMimeType"] == "application/json" and seen["gen"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert r.finish_reason == "length" and r.json() == {"intent": "platform_faq"}


@pytest.mark.asyncio
async def test_retries_on_429_and_5xx_then_succeeds(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", lambda *_: _noop())
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429 if len(calls) == 1 else 503 if len(calls) == 2 else 200, json=ok().json())

    r = await client(handler).chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=10)
    assert r.text == "Hello" and len(calls) == 3


async def _noop():
    return None


@pytest.mark.asyncio
async def test_bad_key_or_model_is_not_retried_and_errors_never_contain_the_key(caplog):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(403, json={"error": {"message": f"API key {KEY} not valid"}})

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(LLMError) as ei:
            await client(handler).chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=10)
    assert len(calls) == 1 and "403" in str(ei.value)
    assert KEY not in str(ei.value) and KEY not in caplog.text


@pytest.mark.asyncio
async def test_network_errors_are_retried_and_reported_by_type_only(monkeypatch):
    monkeypatch.setattr("asyncio.sleep", lambda *_: _noop())

    def handler(req):
        raise httpx.ConnectError(f"cannot reach https://x/?key={KEY}")

    with pytest.raises(LLMError) as ei:
        await client(handler).chat(model="m", messages=[{"role": "user", "content": "x"}], max_tokens=10)
    assert "ConnectError" in str(ei.value) and KEY not in str(ei.value)


@pytest.mark.asyncio
async def test_answer_falls_back_to_the_router_model_and_stream_yields_text_then_done():
    models = []

    def handler(req):
        models.append(str(req.url).split("/models/")[1].split(":")[0])
        return httpx.Response(500) if len(models) <= 3 else ok("short")

    c = client(handler, gemini_answer_model="big", gemini_router_model="small")
    events = [e async for e in c.answer_stream([{"role": "system", "content": "S"}, {"role": "user", "content": "q"}])]
    assert [e[0] for e in events] == ["delta", "done"] and events[0][1] == "short"
    assert models[:3] == ["big"] * 3 and models[3] == "small" and events[1][1].fallback_used


@pytest.mark.asyncio
async def test_empty_candidates_do_not_crash():
    r = await client(lambda req: httpx.Response(200, json={"candidates": [], "usageMetadata": {"promptTokenCount": 3}})).chat(
        model="m", messages=[{"role": "user", "content": "x"}], max_tokens=10)
    assert r.text == "" and r.input_tokens == 3


# ---------------------------------------------------------------------------------------------- provider selection
def test_provider_selection_and_model_routing():
    g = settings()
    assert isinstance(make_llm(g), GeminiLLMClient)
    assert g.answer_model == g.gemini_answer_model and g.router_model == g.gemini_router_model     # every call site reads these
    assert isinstance(make_llm(Settings(usage_log_dir="")), GroqLLMClient)
    assert Settings(usage_log_dir="").answer_model == "openai/gpt-oss-120b"
    with pytest.raises(ValueError):
        Settings(llm_provider="other")


def test_production_requires_the_key_of_the_chosen_provider():
    base = dict(environment="production", jwt_secret="x" * 40, platform_adapter="none", redis_url="redis://r", qdrant_url="http://q",
                cors_origins="https://app.example", usage_log_dir="")
    assert any("GEMINI_API_KEY" in p for p in production_problems(Settings(llm_provider="gemini", gemini_api_key="", **base)))
    assert not any("API_KEY" in p for p in production_problems(Settings(llm_provider="gemini", gemini_api_key="k", **base)))
    assert any("GROQ_API_KEY" in p for p in production_problems(Settings(groq_api_key="", **base)))
