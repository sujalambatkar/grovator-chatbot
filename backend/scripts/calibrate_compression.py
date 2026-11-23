"""Measure context compression: tokens saved and whether the key fact each question needs is retained."""
import asyncio
import sys
from pathlib import Path

from app.config import Settings, get_settings
from app.rag.compress import build_context, compress_hits
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.rag.tokens import count_tokens
from scripts.common import make_principal

# (role, question, phrase that MUST survive in the compressed context)
CASES = [
    ("student", "How do I log in to Grovator?", "username and password"),
    ("student", "I forgot my password, what do I do?", "teacher to reset"),
    ("student", "What is block coding?", "snapping"),
    ("student", "My blocks are not running, what's wrong?", "loose blocks"),
    ("student", "How do I make the robot repeat something 4 times?", "repeat 4 times"),
    ("student", "What is a variable in blocks?", "named box"),
    ("student", "How do I stop the robot before it hits a wall?", "less than 10"),
    ("student", "What sensors come in the IoT kit?", "light sensor"),
    ("student", "The IoT kit will not connect to my computer", "unplug the usb"),
    ("student", "What parts are in the Robotics Foundation kit?", "drive motors"),
    ("student", "My robot is not moving", "battery"),
    ("student", "How do I make the robot follow a black line?", "sees the line"),
    ("student", "Where are my saved programs?", "my projects"),
    ("teacher", "How do I reset a student's password?", "reset password"),
    ("admin", "How do I assign a teacher to a batch?", "assign teacher"),
    ("student", "The LED does not light up", "right way round"),
    ("student", "My square does not close, the robot drifts", "wheels are tight"),
    ("student", "The number on the display never changes", "wrong port"),
    ("student", "The robot loses the line on curves", "lower the speed"),
    ("student", "My program does nothing when I press run", "events block"),
    ("student", "The alarm never stops", "else part"),
    ("student", "The kit status light is red", "unplug the usb"),
]


async def main() -> None:
    base = get_settings()
    r = HybridRetriever(KnowledgeStore(make_client("memory"), "knowledge"))
    await ingest(Path(base.knowledge_dir), r)
    s = Settings(**{k: v for k, v in base.model_dump().items()})
    for arg in [a for x in sys.argv[1:] for a in x.split()]:
        k, v = arg.split("=")
        setattr(s, k, type(getattr(s, k))(v))
    tb = ta = kept = 0
    for role, q, must in CASES:
        res = await r.retrieve(q, make_principal(role), k=4)
        before, _ = build_context(res.hits, 700)
        comp = await compress_hits(res.hits, res.vec, s)
        after, _ = build_context(comp, 700)
        b, a = count_tokens(before), count_tokens(after)
        ok = must.lower() in after.lower()
        tb, ta, kept = tb + b, ta + a, kept + ok
        print(f"{'ok ' if ok else 'LOST'} {b:4} -> {a:4}  ({role}) {q[:52]!r}  need={must!r}")
    print(f"\ncontext tokens avg {tb/len(CASES):.0f} -> {ta/len(CASES):.0f}  ({100*(1-ta/tb):.0f}% less)   key fact kept: {kept}/{len(CASES)}   {sys.argv[1:]}")


asyncio.run(main())
