"""Live end-to-end check: real Groq + embedded Qdrant. Run: python -m scripts.live_smoke"""
import asyncio
from pathlib import Path

from app.config import get_settings
from app.llm.llm_client import make_llm
from app.llm.prompts import ANSWER_SYSTEM
from app.rag.compress import build_context
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from scripts.common import SYNTH_KNOWLEDGE, make_principal

QUESTIONS = [
    ("student", "My robot is not moving, what should I check?"),
    ("student", "How do I make a program that repeats 4 times?"),
    ("student", "What sensors are in the IoT kit?"),
    ("student", "How do I assign a teacher to a batch?"),
]


async def main() -> None:
    s = get_settings()
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "knowledge"))
    await ingest(SYNTH_KNOWLEDGE, r)
    llm = make_llm(s)
    for role, q in QUESTIONS:
        res = await r.retrieve(q, make_principal(role), k=s.knowledge_top_k)
        ctx, used = build_context(res.hits, s.context_token_budget)
        out = await llm.answer([
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"CONTEXT:\n{ctx}\n\nQUESTION: {q}"},
        ])
        print(f"\nQ: {q}\n  conf={res.confidence:.2f} model={out.model} in={out.input_tokens} out={out.output_tokens} ms={out.latency_ms}\nA: {out.text}")


asyncio.run(main())
