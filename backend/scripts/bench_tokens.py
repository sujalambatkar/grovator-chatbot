"""Token/latency benchmark on a FIXED workload against live Groq. Same script is run before and after Phase 5.

Every LLM call is metered by kind (router / planner / answer / summary / extract) with Groq's own `usage`.
Usage:  python -m scripts.bench_tokens [label]      -> prints a table and writes bench_<label>.json
Paced to stay under Groq's free tier (8k tokens/min/model)."""
import asyncio
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

import fakeredis.aioredis
from qdrant_client import AsyncQdrantClient

from app.chat.cache import SemanticCache
from app.chat.service import ChatService
from app.config import get_settings
from app.core.abuse import TokenBudget
from app.core.usage import UsageTracker
from app.llm import prompts as P
from app.llm.llm_client import make_llm
from app.memory.long_term import MemoryManager, MemoryStore
from app.memory.session import SessionStore
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.tools.mock_adapter import MockAdapter
from scripts.common import SYNTH_KNOWLEDGE, make_principal

PACE_S = 7.0
S1 = make_principal("student", user_id="s1", batch_ids=["b1"])
T1 = make_principal("teacher", user_id="t1", batch_ids=["b1"])


def student(n: int):
    return make_principal("student", user_id=f"stu{n}", batch_ids=["b1"])


# (principal, session, message, expected_intent, expected_source_title or None)
FAQ = "platform_faq"
WORKLOAD = [
    # multi-turn conversations (same student, same session)
    (S1, "bench-robot", "What parts come in the robotics kit?", FAQ, "Robotics Foundation Kit Guide"),
    (S1, "bench-robot", "it doesn't move, why?", FAQ, "Robotics Foundation Kit Guide"),
    (S1, "bench-robot", "and how do I make it follow a line?", FAQ, "Robotics Foundation Kit Guide"),
    (S1, "bench-iot", "How do I connect the IoT kit?", FAQ, "IoT Kit Guide"),
    (S1, "bench-iot", "how do I read the temperature one?", FAQ, "IoT Kit Guide"),
    (S1, "bench-iot", "now how do I repeat that 5 times?", FAQ, "Loops and Conditions in Block Coding"),
    (S1, "bench-blocks", "What is block coding?", FAQ, "Block Coding Basics"),
    (S1, "bench-blocks", "what about variables in it?", FAQ, "Loops and Conditions in Block Coding"),
    (S1, "bench-proj", "Where are my saved programs?", FAQ, "Projects, Progress and Getting Help"),
    (S1, "bench-proj", "can I get a deleted one back?", FAQ, "Projects, Progress and Getting Help"),
    (S1, "bench-login", "How do I log in?", FAQ, "Getting Started on Grovator"),
    (S1, "bench-login", "I forgot it", FAQ, "Getting Started on Grovator"),
    # a cohort of different students asking the same FAQs as their first message (cache opportunity)
    (student(1), "co-1", "How do I connect the IoT kit?", FAQ, "IoT Kit Guide"),
    (student(2), "co-2", "how can i connect my iot kit to the computer", FAQ, "IoT Kit Guide"),
    (student(3), "co-3", "What sensors come in the IoT kit?", FAQ, "IoT Kit Guide"),
    (student(4), "co-4", "How do I connect the IoT kit?", FAQ, "IoT Kit Guide"),
    (student(5), "co-5", "My robot is not moving, what should I check?", FAQ, "Robotics Foundation Kit Guide"),
    (student(6), "co-6", "Why is my robot not moving?", FAQ, "Robotics Foundation Kit Guide"),
    (student(7), "co-7", "How do I log in?", FAQ, "Getting Started on Grovator"),
    (student(8), "co-8", "how do i log in to grovator", FAQ, "Getting Started on Grovator"),
    # live data (never cached; answered by code from tools)
    (T1, "bench-t1", "Show Aarav's report", "my_data", None),
    (T1, "bench-t1b", "What is my schedule?", "my_data", None),
    # smalltalk
    (S1, "bench-hi", "hi", "smalltalk", None),
    (S1, "bench-hi", "thanks!", "smalltalk", None),
]


class Metered:
    """Wraps the real client; attributes every call's Groq-reported tokens to a component."""

    def __init__(self, inner) -> None:
        self.inner, self.cur = inner, None
        self.rows: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.turn = -1

    def _kind(self, messages) -> str:
        sys_ = messages[0]["content"]
        return {P.ROUTER_SYSTEM: "router", P.TOOL_PLAN_SYSTEM: "planner", P.SUMMARY_SYSTEM: "summary",
                P.EXTRACT_SYSTEM: "extract"}.get(sys_, "answer")

    def _add(self, kind, res):
        self.rows[self.turn][kind + "_in"] += res.input_tokens
        self.rows[self.turn][kind + "_out"] += res.output_tokens
        self.rows[self.turn]["calls"] += 1

    async def chat(self, **kw):
        res = await self.inner.chat(**kw)
        self._add(self._kind(kw["messages"]), res)
        return res

    async def answer(self, messages):
        res = await self.inner.answer(messages)
        self._add("answer", res)
        return res

    async def answer_stream(self, messages):
        async for kind, val in self.inner.answer_stream(messages):
            if kind == "done":
                self._add("answer", val)
            yield kind, val


def build_service(llm, retriever, s):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mm = MemoryManager(MemoryStore(AsyncQdrantClient(location=":memory:"), "user_memory"), llm, s)
    kwargs = dict(memory=mm, budget=TokenBudget(redis, 10**9, 10**9), adapter=MockAdapter(),
                  cache=SemanticCache(redis, s), tracker=UsageTracker(redis, ""))
    return ChatService(llm, retriever, SessionStore(redis, 600), s, **kwargs), mm, redis


async def main(label: str) -> None:
    s = get_settings()
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "knowledge"))
    await ingest(SYNTH_KNOWLEDGE, r)
    llm = Metered(make_llm(s))
    svc, mm, redis = build_service(llm, r, s)
    await mm.store.ensure()
    out, lat = [], []
    for i, (who, sid, msg, exp_intent, exp_title) in enumerate(WORKLOAD):
        llm.turn = i
        t0 = time.perf_counter()
        events = [e async for e in svc.events(who, msg, sid)]
        ms = (time.perf_counter() - t0) * 1000
        await svc.drain()
        done = [e for e in events if e["type"] == "done"][0]
        row = dict(llm.rows[i])
        total = sum(v for k, v in row.items() if k.endswith(("_in", "_out")))
        ok_intent = done["intent"] == exp_intent or (exp_intent == FAQ and done["intent"] in ("cache", FAQ))
        ok_src = exp_title is None or exp_title in done.get("sources", []) or bool(done.get("cache_hit"))
        lat.append(ms)
        out.append(dict(i=i, msg=msg, intent=done["intent"], cache=done.get("cache_hit"), shortcut=done.get("shortcut"),
                        total=total, latency_ms=int(ms), ok_intent=ok_intent, ok_src=ok_src,
                        refused=done.get("refused_reason"), **row))
        flags = "".join(c for c, on in (("C", done.get("cache_hit")), ("S", done.get("shortcut"))) if on)
        print(f"{i:2} {'ok ' if ok_intent and ok_src else 'BAD'} {done['intent']:12} tok={total:5} {int(ms):5}ms {flags:2} {msg[:48]!r}", flush=True)
        await asyncio.sleep(PACE_S)
    n = len(out)
    comp = ["router", "planner", "answer", "summary", "extract"]
    summary = {c: sum(o.get(c + "_in", 0) + o.get(c + "_out", 0) for o in out) / n for c in comp}
    total_avg = sum(o["total"] for o in out) / n
    lat_sorted = sorted(lat)
    result = dict(label=label, turns=n, avg_tokens_per_turn=total_avg, by_component_avg=summary,
                  answer_in_avg=sum(o.get("answer_in", 0) for o in out) / n,
                  llm_calls_per_turn=sum(o.get("calls", 0) for o in out) / n,
                  p50_ms=statistics.median(lat), p95_ms=lat_sorted[int(0.95 * (n - 1))],
                  quality=dict(intent_ok=sum(o["ok_intent"] for o in out), source_ok=sum(o["ok_src"] for o in out),
                               cache_hits=sum(bool(o["cache"]) for o in out), shortcuts=sum(bool(o["shortcut"]) for o in out),
                               refused=sum(bool(o["refused"]) for o in out)),
                  turns_detail=out)
    Path(f"bench_{label}.json").write_text(json.dumps(result, indent=1))
    print(f"\n[{label}] turns={n} avg_tokens/turn={total_avg:.0f} calls/turn={result['llm_calls_per_turn']:.2f} "
          f"p50={result['p50_ms']:.0f}ms p95={result['p95_ms']:.0f}ms")
    print("  by component (avg/turn):", {k: round(v) for k, v in summary.items()})
    print("  quality:", result["quality"])
    print("  flags:", {k: getattr(s, k) for k in ("enable_semantic_cache", "enable_router_shortcut", "lazy_summary", "enable_compression")})
    sm = await svc.tracker.summary("org1", None, 1)
    print("  tracker cross-check: requests=%d avg_tokens_per_request=%.0f (bench metered: %.0f)" % (sm["requests"], sm["avg_tokens_per_request"], total_avg))


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "run"))
