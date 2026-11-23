"""Qdrant-backed knowledge store. RBAC filters are applied server-side from the Principal."""
from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient, models

from app.core.auth import Principal
from app.rag.chunking import Chunk

VECTOR_SIZE = 384


@dataclass
class Hit:
    id: str
    text: str
    title: str
    source: str
    summary: str
    score: float  # cosine similarity (0 when only BM25 matched)


def scope_filter(p: Principal, source: str | None = None) -> models.Filter:
    """Role visibility + tenant scope. '*' means platform-wide content."""
    must: list[models.Condition] = [
        models.FieldCondition(key="role_visibility", match=models.MatchAny(any=[p.role.value])),
        models.FieldCondition(key="org_id", match=models.MatchAny(any=["*", p.org_id])),
    ]
    school = ["*", p.school_id] if p.school_id else ["*"]
    must.append(models.FieldCondition(key="school_id", match=models.MatchAny(any=school)))
    if source:  # still inside the caller's scope: this only narrows
        must.append(models.FieldCondition(key="source", match=models.MatchValue(value=source)))
    return models.Filter(must=must)


def make_client(url: str) -> AsyncQdrantClient:
    return AsyncQdrantClient(location=":memory:") if url == "memory" else AsyncQdrantClient(url=url)


class KnowledgeStore:
    def __init__(self, client: AsyncQdrantClient, collection: str) -> None:
        self.client, self.collection = client, collection

    async def ensure(self, recreate: bool = False) -> None:
        exists = await self.client.collection_exists(self.collection)
        if exists and recreate:
            await self.client.delete_collection(self.collection)
            exists = False
        if not exists:
            await self.client.create_collection(
                self.collection,
                vectors_config=models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
            )
            for key in ("role_visibility", "org_id", "school_id", "source"):
                await self.client.create_payload_index(
                    self.collection, key, models.PayloadSchemaType.KEYWORD
                )

    async def upsert(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        await self.client.upsert(
            self.collection,
            points=[models.PointStruct(id=c.id, vector=v, payload=c.payload()) for c, v in zip(chunks, vectors)],
        )

    async def vector_search(self, vec: list[float], p: Principal, limit: int, source: str | None = None) -> list[Hit]:
        res = await self.client.query_points(
            self.collection, query=vec, query_filter=scope_filter(p, source), limit=limit, with_payload=True
        )
        return [_hit(pt.id, pt.payload, pt.score) for pt in res.points]

    async def scoped_chunks(self, p: Principal) -> list[Hit]:
        out: list[Hit] = []
        offset = None
        while True:
            pts, offset = await self.client.scroll(
                self.collection, scroll_filter=scope_filter(p), limit=256, offset=offset, with_payload=True
            )
            out += [_hit(pt.id, pt.payload, 0.0) for pt in pts]
            if offset is None:
                return out


    async def all_texts(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        offset = None
        while True:
            pts, offset = await self.client.scroll(self.collection, limit=256, offset=offset, with_payload=True)
            out += [(str(pt.id), pt.payload["text"]) for pt in pts]
            if offset is None:
                return out


def _hit(pid, payload: dict, score: float) -> Hit:
    return Hit(
        id=str(pid), text=payload["text"], title=payload["title"], source=payload["source"],
        summary=payload["summary"], score=float(score),
    )
