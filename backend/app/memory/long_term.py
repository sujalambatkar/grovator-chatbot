"""Long-term memory inside the RAG pipeline: extract -> PII filter -> dedupe -> embed -> `user_memory`.

Every read and write is hard-filtered by (user_id, org_id) taken from the JWT Principal.
There is no code path that takes a user_id from a request body or from the LLM.
"""
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from qdrant_client import AsyncQdrantClient, models

from app.config import Settings
from app.core.auth import Principal
from app.guardrails.injection import looks_like_injection
from app.guardrails.pii import detect_sensitive, redact
from app.llm.llm_client import LLMClient
from app.llm.prompts import EXTRACT_SYSTEM
from app.rag.embeddings import embed_docs
from app.rag.store import VECTOR_SIZE

log = logging.getLogger("govator.memory")

MEMORY_TYPES = {"preference", "context", "goal", "skill"}
MAX_CANDIDATES = 3
MAX_TEXT_CHARS = 120

# Free pre-filter: only spend an extractor call when the user is talking about themselves.
_SELF_STATEMENT = re.compile(
    r"\b(?:i am|i'm|im|i prefer|i like|i love|i want|i need|i'd like|we are|we're|remember|from now on|"
    r"always (?:use|give|show|explain)|please (?:use|give|keep|explain)|my (?:goal|favou?rite|batch|class|team|level|"
    r"project|group)\b)", re.I)


@dataclass
class Memory:
    id: str
    text: str
    type: str
    created_at: str
    last_used_at: str
    score: float = 0.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _owner(p: Principal) -> list[models.Condition]:
    return [
        models.FieldCondition(key="user_id", match=models.MatchValue(value=p.user_id)),
        models.FieldCondition(key="org_id", match=models.MatchValue(value=p.org_id)),
    ]


def _mem(pid, payload: dict, score: float = 0.0) -> Memory:
    return Memory(str(pid), payload["text"], payload["type"], payload["created_at"], payload["last_used_at"], score)


class MemoryStore:
    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self.client, self.collection = client, collection

    async def ensure(self) -> None:
        if not await self.client.collection_exists(self.collection):
            await self.client.create_collection(
                self.collection, vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE))
            for key in ("user_id", "org_id"):
                await self.client.create_payload_index(self.collection, key, models.PayloadSchemaType.KEYWORD)

    async def search(self, vec: list[float], p: Principal, limit: int) -> list[Memory]:
        res = await self.client.query_points(
            self.collection, query=vec, query_filter=models.Filter(must=_owner(p)), limit=limit, with_payload=True)
        return [_mem(pt.id, pt.payload, pt.score) for pt in res.points]

    async def upsert(self, mem_id: str, vec: list[float], payload: dict) -> None:
        await self.client.upsert(self.collection, points=[models.PointStruct(id=mem_id, vector=vec, payload=payload)])

    async def list_all(self, p: Principal) -> list[Memory]:
        pts, _ = await self.client.scroll(
            self.collection, scroll_filter=models.Filter(must=_owner(p)), limit=1000, with_payload=True)
        return sorted((_mem(pt.id, pt.payload) for pt in pts), key=lambda m: (m.created_at, m.text))

    async def delete(self, p: Principal, mem_id: str) -> bool:
        flt = models.Filter(must=[*_owner(p), models.HasIdCondition(has_id=[mem_id])])
        pts, _ = await self.client.scroll(self.collection, scroll_filter=flt, limit=1)
        if not pts:
            return False  # missing OR owned by someone else: indistinguishable on purpose
        await self.client.delete(self.collection, points_selector=models.FilterSelector(filter=flt))
        return True

    async def delete_all(self, p: Principal) -> int:
        n = len(await self.list_all(p))
        await self.client.delete(self.collection, points_selector=models.FilterSelector(
            filter=models.Filter(must=_owner(p))))
        return n

    async def touch(self, p: Principal, ids: list[str]) -> None:
        flt = models.Filter(must=[*_owner(p), models.HasIdCondition(has_id=ids)])
        await self.client.set_payload(self.collection, payload={"last_used_at": _now()}, points=models.FilterSelector(filter=flt))


class MemoryManager:
    def __init__(self, store: MemoryStore, llm: LLMClient, settings: Settings) -> None:
        self.store, self.llm, self.s = store, llm, settings
        self.on_usage = None  # optional async callback(principal, tokens) for budget accounting

    @staticmethod
    def should_extract(message: str) -> bool:
        return bool(_SELF_STATEMENT.search(message))

    async def retrieve(self, vec: list[float], p: Principal) -> list[Memory]:
        hits = await self.store.search(vec, p, self.s.memory_top_k)
        out = [m for m in hits if m.score >= self.s.memory_min_score]
        # Style preferences ("short answers") are unrelated to any specific question, so similarity alone
        # would never surface them: let the most recently used one ride along if there is room.
        if len(out) < self.s.memory_top_k:
            prefs = [m for m in await self.store.list_all(p) if m.type == "preference" and m.id not in {o.id for o in out}]
            if prefs:
                out.append(max(prefs, key=lambda m: m.last_used_at))
        return out

    async def extract(self, message: str) -> tuple[list[tuple[str, str]], int]:
        """Small-model extraction. The message is PII-redacted before it reaches the LLM."""
        res = await self.llm.chat(
            model=self.s.router_model,
            messages=[{"role": "system", "content": EXTRACT_SYSTEM},
                      {"role": "user", "content": f"USER MESSAGE: {redact(message)}"}],
            max_tokens=250, json_mode=True, temperature=0)
        tokens = res.input_tokens + res.output_tokens
        try:
            raw = json.loads(res.text).get("memories", [])
        except (ValueError, AttributeError):
            return [], tokens
        out: list[tuple[str, str]] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            text, typ = str(item.get("text", "")).strip(), str(item.get("type", "")).strip().lower()
            if not text or len(text) > MAX_TEXT_CHARS or typ not in MEMORY_TYPES:
                continue
            if detect_sensitive(text) or looks_like_injection(text):  # PII check BEFORE any write
                log.info("memory_candidate_dropped")  # never log the text
                continue
            out.append((text, typ))
        return out[:MAX_CANDIDATES], tokens

    async def save(self, p: Principal, candidates: list[tuple[str, str]], session_id: str) -> int:
        """Embed, dedupe (cosine > threshold -> update in place), insert. Returns number of writes."""
        if not candidates:
            return 0
        vecs = await embed_docs([t for t, _ in candidates])
        writes = 0
        for (text, typ), vec in zip(candidates, vecs):
            now = _now()
            near = await self.store.search(vec, p, 1)  # same user + org only
            if near and near[0].score > self.s.memory_dedupe_score:
                m = near[0]
                await self.store.upsert(m.id, vec, self._payload(p, text, typ, m.created_at, now, session_id))
            else:
                if len(await self.store.list_all(p)) >= self.s.memory_max_per_user:
                    oldest = min(await self.store.list_all(p), key=lambda m: m.last_used_at)
                    await self.store.delete(p, oldest.id)
                await self.store.upsert(str(uuid.uuid4()), vec, self._payload(p, text, typ, now, now, session_id))
            writes += 1
        return writes

    @staticmethod
    def _payload(p: Principal, text: str, typ: str, created: str, used: str, session_id: str) -> dict:
        return {"user_id": p.user_id, "org_id": p.org_id, "text": text, "type": typ,
                "created_at": created, "last_used_at": used, "source_session": session_id}

    async def extract_and_save(self, p: Principal, message: str, session_id: str) -> int:
        if not self.should_extract(message):
            return 0
        candidates, tokens = await self.extract(message)
        if self.on_usage:
            await self.on_usage(p, tokens)
        return await self.save(p, candidates, session_id)
