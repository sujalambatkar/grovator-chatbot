"""Measured token comparison on live Groq: the optimized pipeline vs naive chatbots, same conversation.

  A  this project   : cache + router shortcut + RAG (page-aware) + compression + 2-turn window + lazy summary
  B  naive, page    : sends the open page + the whole chat so far with every message (REAL calls, measured)
  C  naive, whole site: sends every course page/block/tool + the whole chat each message (token count ESTIMATED
                        locally; plus ONE real attempt, because it may not even fit Groq's free-tier limit)

Run: python -m scripts.demo_compare"""
import asyncio
import re
from pathlib import Path

import fakeredis.aioredis
from groq import AsyncGroq
from qdrant_client import AsyncQdrantClient

from app.chat.cache import SemanticCache
from app.chat.service import ChatService
from app.config import Settings
from app.content.catalog import ContentCatalog
from app.core.abuse import TokenBudget
from app.core.usage import UsageTracker
from app.llm import prompts as P
from app.llm.llm_client import GroqLLMClient
from app.memory.long_term import MemoryManager, MemoryStore
from app.memory.session import SessionStore
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.rag.tokens import count_tokens
from app.tools.mock_adapter import MockAdapter
from scripts.common import make_principal

PAGE = "devices/ultrasonic_sensor"
PACE = 9.0
CONVO = [
    "How do I connect this to the gateway?",
    "What should I do after connecting it?",
    "Where does the VCC wire go?",
    "What if it is not working?",
    "How do I connect the I2C LCD?",                            # a DIFFERENT device page than the open one
    "Which pins does it use again?",                            # needs memory of this chat (the LCD)
    "Write me a poem about pizza",                              # off-topic: must be refused
]


class Meter:
    def __init__(self, inner):
        self.inner, self.tokens = inner, 0

    def take(self):
        t, self.tokens = self.tokens, 0
        return t

    async def chat(self, **kw):
        r = await self.inner.chat(**kw)
        self.tokens += r.input_tokens + r.output_tokens
        return r

    async def answer(self, m):
        r = await self.inner.answer(m)
        self.tokens += r.input_tokens + r.output_tokens
        return r

    async def answer_stream(self, m):
        async for k, v in self.inner.answer_stream(m):
            if k == "done":
                self.tokens += v.input_tokens + v.output_tokens
            yield k, v


async def main() -> None:
    s = Settings()
    me = make_principal("student", user_id="s1", batch_ids=["b1"])
    catalog = ContentCatalog(Path(s.knowledge_dir))
    page = catalog.get(me, PAGE)
    retriever = HybridRetriever(KnowledgeStore(make_client("memory"), "knowledge"))
    await ingest(Path(s.knowledge_dir), retriever)
    llm = Meter(GroqLLMClient(s))
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    mm = MemoryManager(MemoryStore(AsyncQdrantClient(location=":memory:"), "m"), llm, s)
    await mm.store.ensure()
    svc = ChatService(llm, retriever, SessionStore(redis, 600), s, mm, TokenBudget(redis, 10**9, 10**9), MockAdapter(),
                      SemanticCache(redis, s), UsageTracker(redis, ""), catalog)
    raw = AsyncGroq(api_key=s.groq_api_key)
    site = "\n\n".join(f"## {d.title}\n{d.body}" for d in catalog.visible(me))
    print(f"Course site for this user: {len(catalog.visible(me))} pages, {count_tokens(site):,} tokens. Open page: {page.title} ({page.tokens} tokens).\n")

    hist_b, rows = "", []
    for i, q in enumerate(CONVO, 1):
        # ---- A: this project
        events = [e async for e in svc.events(me, q, "compare-session-1", PAGE)]
        await svc.drain()
        done = next(e for e in events if e["type"] == "done")
        a_tokens = llm.take()
        # ---- B: naive, open page + whole chat so far (real call)
        msgs = [{"role": "system", "content": P.ANSWER_SYSTEM},
                {"role": "user", "content": f"PAGE:\n{page.body}\n\nCHAT SO FAR:\n{hist_b or '(none)'}\n\nQUESTION: {q}"}]
        r = await raw.chat.completions.create(model=s.answer_model, messages=msgs, max_tokens=s.answer_max_tokens, reasoning_effort="low")
        b_tokens, b_ans = r.usage.prompt_tokens + r.usage.completion_tokens, (r.choices[0].message.content or "").strip()
        hist_b += f"User: {q}\nAssistant: {b_ans}\n"
        rows.append((q, a_tokens, b_tokens, done["naive_tokens"], done["answer"], b_ans, done.get("refused_reason"), done.get("cache_hit")))
        print(f"turn {i}: ours={a_tokens:>5}  naive(page)={b_tokens:>5}  naive(site, est)={done['naive_tokens']:>6,}   {q}", flush=True)
        await asyncio.sleep(PACE)

    print("\n=== ONE real attempt at the whole-site naive approach (turn 1 prompt): does it even fit the free tier? ===")
    try:
        r = await raw.chat.completions.create(model=s.answer_model, max_tokens=200, reasoning_effort="low", messages=[
            {"role": "system", "content": P.ANSWER_SYSTEM}, {"role": "user", "content": f"SITE:\n{site}\n\nQUESTION: {CONVO[0]}"}])
        print(f"accepted: used {r.usage.prompt_tokens + r.usage.completion_tokens:,} tokens for ONE message")
    except Exception as e:  # noqa: BLE001
        msg = re.sub(r"\s+", " ", str(e))[:330]
        print(f"REJECTED by Groq ({type(e).__name__}): {msg}")

    A, B, C = sum(r[1] for r in rows), sum(r[2] for r in rows), sum(r[3] for r in rows)
    print("\n=== TOTALS over", len(CONVO), "messages ===")
    print(f"A this project             : {A:>7,} tokens   (measured)")
    print(f"B naive, page + full chat  : {B:>7,} tokens   (measured)   -> A uses {100 * (1 - A / B):.0f}% fewer tokens than B")
    print(f"C naive, whole site + chat : {C:>7,} tokens   (estimated)  -> A uses {100 * (1 - A / C):.0f}% fewer tokens than C")
    print("(B only sees the open page, so it cannot answer questions about other lessons, blocks or courses; C can, at that cost.)")
    print("\n=== ANSWERS (A = this project | B = naive page-only) ===")
    for q, a, b, c, ans_a, ans_b, why, cached in rows:
        print(f"\nQ: {q}\n  A [{a} tok{', ' + why if why else ''}{', cache' if cached else ''}]: {ans_a[:210]!r}\n  B [{b} tok]: {ans_b[:210]!r}")


asyncio.run(main())
