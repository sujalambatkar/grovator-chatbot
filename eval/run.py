"""`python -m eval.run` : pass/fail table for the application assistant (eval/questions.yaml).

  python -m eval.run                     # live: real LLM for the questions that need one (uses your Groq/Gemini quota)
  python -m eval.run --offline           # only the `det: true` questions, no LLM at all (free, deterministic)
  python -m eval.run --only wire-1,gap-1 --pace 8

Run from the repository root with the backend's Python: backend/.venv/bin/python -m eval.run
Exit code 1 if any `det` question fails (those are pure code paths), or --min-pass is not reached.
"""
import argparse
import asyncio
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
BACK = ROOT / "backend"
sys.path.insert(0, str(BACK))

import fakeredis.aioredis  # noqa: E402
from qdrant_client import AsyncQdrantClient  # noqa: E402

from app.chat.cache import SemanticCache  # noqa: E402
from app.chat.service import ChatService  # noqa: E402
from app.config import Settings  # noqa: E402
from app.core.abuse import TokenBudget  # noqa: E402
from app.core.auth import Principal, Role  # noqa: E402
from app.core.usage import UsageTracker  # noqa: E402
from app.llm.llm_client import LLMError, make_llm  # noqa: E402
from app.memory.long_term import MemoryManager, MemoryStore  # noqa: E402
from app.memory.session import SessionStore  # noqa: E402
from app.rag.ingest import ingest  # noqa: E402
from app.rag.retriever import HybridRetriever  # noqa: E402
from app.rag.store import KnowledgeStore, make_client  # noqa: E402
from app.tools.mock_adapter import MockAdapter  # noqa: E402

STUDENT = Principal(user_id="eval-student", role=Role.student, org_id="org1", school_id="sch1", batch_ids=["b1"])


class NoLLM:
    """Offline mode: any model call is a test failure (the question was supposed to be answered from the files)."""
    calls = 0

    async def chat(self, **kw):
        NoLLM.calls += 1
        raise LLMError("offline eval: model call attempted")

    async def answer_stream(self, messages):
        NoLLM.calls += 1
        raise LLMError("offline eval: model call attempted")
        yield  # pragma: no cover


class Counting:
    def __init__(self, inner) -> None:
        self.inner, self.calls = inner, 0

    async def chat(self, **kw):
        self.calls += 1
        return await self.inner.chat(**kw)

    async def answer_stream(self, messages):
        self.calls += 1
        async for x in self.inner.answer_stream(messages):
            yield x


def haystack(d: dict) -> str:
    parts = [d.get("answer", "")]
    t = d.get("connection_table")
    if t:
        parts += [f"{r['sensor_pin']} {r['kit_pin']} {r['note']}" for r in t["rows"]]
    parts += [b["title"] for b in d.get("block_stack") or []]
    parts += [c["code"] for c in d.get("code_blocks") or []]
    return "\n".join(parts).replace("\u2019", "'").replace("\u202f", " ").replace("\u2011", "-")  # curly quotes etc. from the model


def judge(q: dict, d: dict, llm_calls: int) -> list[str]:
    """Return the list of failed checks (empty = pass)."""
    text, low, fails = haystack(d), haystack(d).lower(), []
    for s in q.get("must_all", []):
        if s.lower() not in low:
            fails.append(f"missing '{s}'")
    if q.get("must_any") and not any(s.lower() in low for s in q["must_any"]):
        fails.append(f"none of {q['must_any']}")
    for s in q.get("must_not", []):
        if s.lower() in low:
            fails.append(f"contains forbidden '{s}'")
    for rx in q.get("must_not_re", []):
        if re.search(rx, text, re.S):
            fails.append(f"matches forbidden /{rx}/")
    if q.get("det") and llm_calls:
        fails.append(f"used the model ({llm_calls} calls), expected 0")
    if q.get("intent") and d.get("intent") != q["intent"]:
        fails.append(f"intent {d.get('intent')} != {q['intent']}")
    if q.get("no_table") and d.get("connection_table"):
        fails.append("returned a wiring table")
    if q.get("not_documented") and ("don't have that detail" not in d["answer"]):
        fails.append("did not say 'I don't have that detail yet'")
    return fails


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--only", default="")
    ap.add_argument("--pace", type=float, default=5.0, help="seconds between model calls (free-tier rate limits)")
    ap.add_argument("--min-pass", type=float, default=0.0, help="fail the run below this pass rate (0-1)")
    ap.add_argument("-v", "--verbose", action="store_true", help="print each answer")
    a = ap.parse_args()

    items = yaml.safe_load((Path(__file__).parent / "questions.yaml").read_text(encoding="utf-8"))
    only = {x for x in a.only.split(",") if x}
    items = [q for q in items if (not only or q["id"] in only) and (not a.offline or q.get("det"))]

    s = Settings(_env_file=str(BACK / ".env"), usage_log_dir="", enable_semantic_cache=False)
    llm = NoLLM() if a.offline else Counting(make_llm(s))
    if not a.offline and not s.llm_api_key:
        print("No LLM key (backend/.env). Use --offline for the deterministic questions.")
        return 2
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "eval_kb"))
    n = await ingest(Path(s.knowledge_dir), r, app_root=Path(s.app_knowledge_dir))
    print(f"eval | {'offline (no LLM)' if a.offline else 'live, model ' + s.answer_model} | {n} chunks | {len(items)} questions\n")

    results = []
    for q in items:
        redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
        mm = MemoryManager(MemoryStore(AsyncQdrantClient(location=":memory:"), "m"), llm, s)
        svc = ChatService(llm, r, SessionStore(redis, 600), s, mm, TokenBudget(redis, 10**9, 10**9), MockAdapter(),
                          cache=SemanticCache(redis, s), tracker=UsageTracker(redis, ""))
        sid, done, before, t0 = f"eval-{q['id']}", {}, 0, time.perf_counter()
        try:
            for msg in [*q.get("prior", []), q["q"]]:
                before = getattr(llm, "calls", 0) if not a.offline else NoLLM.calls
                evs = [e async for e in svc.events(STUDENT, msg, sid)]
                await svc.drain()
                done = next((e for e in evs if e["type"] == "done"), {"answer": "(error: " + str(evs[-1].get("message")) + ")", "tokens_total": 0})
                if not a.offline and not q.get("det"):
                    await asyncio.sleep(a.pace)
            calls = (getattr(llm, "calls", 0) if not a.offline else NoLLM.calls) - before
            fails = judge(q, done, calls)
        except Exception as exc:  # noqa: BLE001
            done, fails = {"answer": f"(exception {type(exc).__name__})", "tokens_total": 0}, [f"exception {type(exc).__name__}: {exc}"]
        results.append((q, done, fails, int((time.perf_counter() - t0) * 1000)))
        if a.verbose:
            print(f"  [{q['id']}] {q['q']}\n     -> {done['answer'][:300]!r}")

    print(f"{'id':9} {'tag':14} {'det':3} {'result':6} {'tokens':>7} {'ms':>6}  note")
    print("-" * 100)
    by_tag: dict[str, list[bool]] = defaultdict(list)
    for q, d, f, ms in results:
        by_tag[q["tag"]].append(not f)
        print(f"{q['id']:9} {q['tag']:14} {'y' if q.get('det') else '':3} {'PASS' if not f else 'FAIL':6} {d.get('tokens_total', 0) or 0:>7} {ms:>6}  {'; '.join(f)[:70]}")
    ok = sum(not f for _, _, f, _ in results)
    print("\nby tag: " + ", ".join(f"{t} {sum(v)}/{len(v)}" for t, v in by_tag.items()))
    toks = [d.get("tokens_total", 0) or 0 for _, d, _, _ in results]
    print(f"TOTAL {ok}/{len(results)} passed ({ok / max(1, len(results)):.0%}) | answered with 0 tokens: {sum(t == 0 for t in toks)} | avg tokens/question {sum(toks) / max(1, len(toks)):.0f}")
    det_fail = [q["id"] for q, _, f, _ in results if f and q.get("det")]
    if det_fail:
        print("DETERMINISTIC FAILURES:", ", ".join(det_fail))
    return 1 if det_fail or ok / max(1, len(results)) < a.min_pass else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
