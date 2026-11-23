import json
import os
import time
from pathlib import Path

# Phase 2-4 suites assert baseline behaviour: run them with the Phase 5 optimisations off.
# Phase 5 tests switch them on explicitly via Settings(...).
for _flag in ("ENABLE_SEMANTIC_CACHE", "ENABLE_ROUTER_SHORTCUT", "LAZY_SUMMARY", "ENABLE_COMPRESSION"):
    os.environ[_flag] = "false"
os.environ["USAGE_LOG_DIR"] = ""
os.environ["SUMMARY_MODE"] = "llm"          # legacy suites assert LLM summaries
os.environ["LLM_PROVIDER"] = "groq"          # a developer's .env may select Gemini; tests pin the default
os.environ["DEMO_MODE"] = "false"           # a developer's local .env must never change test results

import jwt
import pytest
import pytest_asyncio

from app.config import get_settings
from app.core.auth import Principal, Role
from app.llm.llm_client import LLMResult
from app.llm.prompts import EXTRACT_SYSTEM, ROUTER_SYSTEM, SUMMARY_SYSTEM, TOOL_PLAN_SYSTEM
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.rag.tokens import count_tokens

# The mechanics suites run against a small SYNTHETIC corpus (a fixture), not the real gateway dataset in knowledge_src/.
# Hardware behaviour is tested separately (test_hardware.py) against the real dataset.
KNOWLEDGE_DIR = Path(__file__).resolve().parent / "fixtures" / "knowledge_synthetic"
os.environ["KNOWLEDGE_DIR"] = str(KNOWLEDGE_DIR)


class FakeLLM:
    """Scripted stand-in for Groq. Records calls; never hits the network.

    router_fn(user_content) -> dict lets a test script the router's JSON (default: platform_faq,
    query = the raw message).
    """

    def __init__(self, reply: str = "Fake answer.", router_fn=None, extractor_fn=None, planner_fn=None) -> None:
        self.planner_fn = planner_fn
        self.plan_calls: list[str] = []
        self.extractor_fn = extractor_fn
        self.extract_calls: list[str] = []                  # extractor user contents
        self.reply = reply
        self.router_fn = router_fn
        self.calls: list[list[dict[str, str]]] = []        # answer-model prompts
        self.router_calls: list[str] = []                   # router user contents
        self.summary_calls: list[str] = []                  # summary user contents
        self.router_error: Exception | None = None

    async def chat(self, *, model, messages, max_tokens, json_mode=False, temperature=0.2, reasoning_effort=None):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        if sys_ == ROUTER_SYSTEM:
            if self.router_error:
                raise self.router_error
            self.router_calls.append(user)
            msg = user.split("MESSAGE: ", 1)[1]
            data = self.router_fn(user) if self.router_fn else {"intent": "platform_faq", "query": msg}
            text = json.dumps(data)
        elif sys_ == SUMMARY_SYSTEM:
            self.summary_calls.append(user)
            text = f"Summary: {user.split('USER: ', 1)[1].splitlines()[0]}"
        elif sys_ == TOOL_PLAN_SYSTEM:
            self.plan_calls.append(user)
            text = json.dumps(self.planner_fn(user) if self.planner_fn else {"tool": "none"})
        elif sys_ == EXTRACT_SYSTEM:
            self.extract_calls.append(user)
            text = json.dumps(self.extractor_fn(user) if self.extractor_fn else {"memories": []})
        else:
            raise AssertionError("unexpected chat() call")
        return LLMResult(text=text, model=model, input_tokens=count_tokens(user), output_tokens=count_tokens(text), latency_ms=1)

    def _result(self, messages) -> LLMResult:
        return LLMResult(
            text=self.reply, model="fake-120b",
            input_tokens=sum(count_tokens(m["content"]) for m in messages),
            output_tokens=count_tokens(self.reply), latency_ms=1,
        )

    async def answer(self, messages):
        self.calls.append(messages)
        return self._result(messages)

    async def answer_stream(self, messages):
        self.calls.append(messages)
        for word in self.reply.split(" "):
            yield "delta", word + " "
        yield "done", self._result(messages)


@pytest_asyncio.fixture(scope="session")
async def retriever() -> HybridRetriever:
    s = get_settings()
    r = HybridRetriever(KnowledgeStore(make_client("memory"), s.knowledge_collection))
    await ingest(KNOWLEDGE_DIR, r)
    return r


def make_principal(role: str, **kw) -> Principal:
    base = dict(user_id=f"u-{role}", role=Role(role), org_id="org1", school_id="sch1", batch_ids=["b1"])
    return Principal(**{**base, **kw})


def make_token(role: str, **kw) -> str:
    claims = {"sub": f"u-{role}", "role": role, "org_id": "org1", "school_id": "sch1",
              "batch_ids": ["b1"], "exp": int(time.time()) + 3600, **kw}
    return jwt.encode(claims, get_settings().jwt_secret, algorithm=get_settings().jwt_algorithm)


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


def all_llm_calls(llm: "FakeLLM") -> int:
    """Total LLM invocations of any kind (router, planner, summary, extractor, answer)."""
    return len(llm.calls) + len(llm.router_calls) + len(llm.plan_calls) + len(llm.summary_calls) + len(llm.extract_calls)
