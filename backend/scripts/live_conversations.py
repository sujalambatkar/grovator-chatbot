"""Live multi-turn check against real Groq. Follow-ups are raw (no rewriting by us): the router must resolve them.
Run: python -m scripts.live_conversations"""
import asyncio
import statistics

import fakeredis.aioredis

from app.chat.service import ChatService
from app.config import get_settings
from app.llm.llm_client import make_llm
from app.memory.session import SessionStore
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from scripts.common import SYNTH_KNOWLEDGE, make_principal
import app.chat.service as svcmod
from app.guardrails.output import support_ratio

RATIOS: list[float] = []
_orig = svcmod.check_output


def _spy(answer, **kw):
    if kw.get('source_text') and answer.strip():
        RATIOS.append(support_ratio(answer, kw['source_text']))
    return _orig(answer, **kw)


svcmod.check_output = _spy

# (message, expected source title)
CONVS = {
    "robot_debug": [("What parts come in the robotics kit?", "Robotics Foundation Kit Guide"),
                    ("it doesn't move, why?", "Robotics Foundation Kit Guide"),
                    ("and how do I make it follow a line?", "Robotics Foundation Kit Guide")],
    "iot_to_blocks": [("How do I connect the IoT kit?", "IoT Kit Guide"),
                      ("how do I read the temperature one?", "IoT Kit Guide"),
                      ("now how do I repeat that 5 times?", "Loops and Conditions in Block Coding")],
    "blocks_vars": [("What is block coding?", "Block Coding Basics"),
                    ("what about variables in it?", "Loops and Conditions in Block Coding")],
    "projects": [("Where are my saved programs?", "Projects, Progress and Getting Help"),
                 ("can I get a deleted one back?", "Projects, Progress and Getting Help")],
    "login": [("How do I log in?", "Getting Started on Grovator"),
              ("I forgot it", "Getting Started on Grovator")],
}


async def main() -> None:
    s = get_settings()
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "knowledge"))
    await ingest(SYNTH_KNOWLEDGE, r)
    svc = ChatService(make_llm(s), r, SessionStore(fakeredis.aioredis.FakeRedis(decode_responses=True), 600), s)
    p, ok, total, ins, lat = make_principal("student"), 0, 0, [], []
    for name, turns in CONVS.items():
        print(f"\n== {name}")
        for i, (msg, title) in enumerate(turns):
            done = [e async for e in svc.events(p, msg, f"live-{name}-0001") if e["type"] == "done"][0]
            await svc.drain()
            hit = title in done["sources"]
            ok, total = ok + hit, total + 1
            ins.append(done["input_tokens"])
            print(f"  {'PASS' if hit else 'FAIL'} [{done['intent']}] {msg!r}\n       rewrite: {done.get('standalone_query')!r}\n"
                  f"       sources={done['sources']} in={done['input_tokens']} out={done['output_tokens']} router={done['router_tokens']} truncated={done.get('truncated')} refused={done.get('refused_reason')} support={RATIOS[-1] if RATIOS else 0:.2f}")
    print(f"support ratio min={min(RATIOS):.2f} avg={statistics.mean(RATIOS):.2f}")
    print(f"\nPASS {ok}/{total}; answer input tokens avg={statistics.mean(ins):.0f} max={max(ins)}")


asyncio.run(main())
