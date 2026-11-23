"""Hybrid retrieval: Qdrant vector search + BM25, fused with reciprocal rank fusion (RRF)."""
import hashlib
import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from app.core.auth import Principal
from app.rag.embeddings import embed_query
from app.rag.store import Hit, KnowledgeStore

_TOK = re.compile(r"[a-z0-9]+")
RRF_K = 60
ALSO_WEIGHT = 2.0
PIN_TIE_TOP = 6  # the open page wins a tie only if one of its chunks is ALREADY among the top hybrid candidates


def _tokens(text: str) -> list[str]:
    return _TOK.findall(text.lower())


@dataclass
class Retrieval:
    hits: list[Hit]
    confidence: float  # best cosine similarity among scoped results
    vec: list[float]   # query embedding, reused for user-memory retrieval


class HybridRetriever:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store
        self._bm25_cache: dict[tuple, tuple[BM25Okapi, list[Hit]]] = {}
        self.version = "0"  # content hash of the knowledge base; bumps invalidate the semantic cache

    def invalidate(self) -> None:
        self._bm25_cache.clear()

    async def refresh_version(self) -> str:
        """Hash every stored chunk (id + text). Used at startup against a persistent store."""
        h = hashlib.sha1()
        for pid, text in sorted(await self.store.all_texts()):
            h.update(pid.encode())
            h.update(text.encode())
        self.version = h.hexdigest()[:12]
        return self.version

    async def _bm25(self, p: Principal) -> tuple[BM25Okapi, list[Hit]] | None:
        key = (p.role.value, p.org_id, p.school_id)
        if key not in self._bm25_cache:
            chunks = await self.store.scoped_chunks(p)
            if not chunks:
                return None
            self._bm25_cache[key] = (BM25Okapi([_tokens(c.text) for c in chunks]), chunks)
        return self._bm25_cache[key]

    async def retrieve(self, query: str, p: Principal, k: int = 4, vec: list[float] | None = None,
                       pin_source: str | None = None, pin_first: bool = False,
                       also: list[tuple[str, list[float]]] | None = None) -> Retrieval:
        vec = vec if vec is not None else await embed_query(query)
        vec_hits = await self.store.vector_search(vec, p, limit=12)
        scores = {h.id: h.score for h in vec_hits}

        fused: dict[str, float] = {}
        by_id: dict[str, Hit] = {h.id: h for h in vec_hits}
        for rank, h in enumerate(vec_hits):
            fused[h.id] = fused.get(h.id, 0) + 1 / (RRF_K + rank)

        bm = await self._bm25(p)

        def add_bm25(text: str, w: float = 1.0) -> None:
            if bm is not None and (q := _tokens(text)):
                index, chunks = bm
                bm_scores = index.get_scores(q)
                order = sorted(range(len(chunks)), key=lambda i: bm_scores[i], reverse=True)[:12]
                for rank, i in enumerate([i for i in order if bm_scores[i] > 0]):
                    c = chunks[i]
                    by_id.setdefault(c.id, c)
                    fused[c.id] = fused.get(c.id, 0) + w / (RRF_K + rank)

        add_bm25(query)
        # Extra (text, vector) queries, e.g. the NEW message alone next to "previous question + new message". The new message
        # is the user's actual intent (the old question only disambiguates "it"), so it counts double, in vector AND keyword
        # search: a topic shift inside a follow-up is not drowned by the old topic, and the old topic is still there.
        for etext, evec in also or []:
            for rank, h in enumerate(await self.store.vector_search(evec, p, limit=12)):
                by_id.setdefault(h.id, h)
                scores[h.id] = max(scores.get(h.id, 0.0), h.score)
                fused[h.id] = fused.get(h.id, 0) + ALSO_WEIGHT / (RRF_K + rank)
            add_bm25(etext, ALSO_WEIGHT)

        ranked = sorted(fused, key=fused.get, reverse=True)
        if pin_source:
            # The user is reading this page: make sure its best chunks are in context (best overall hit stays first).
            pinned = [h for h in vec_hits if h.source == pin_source] or await self.store.vector_search(vec, p, 3, source=pin_source)
            for h in pinned:
                by_id.setdefault(h.id, h)
                scores.setdefault(h.id, h.score)
            pins = [h.id for h in pinned[:2]]
            others = [scores[i] for i in ranked if i not in pins and i in scores]
            # Lead with the page when the question points at it ("this lesson"), OR when the page is about as relevant
            # as the best match elsewhere ("what are the common mistakes?"): ties go to what the user is reading.
            pin_score = max((scores[i] for i in pins), default=0.0)
            candidate = any(i in ranked[:PIN_TIE_TOP] for i in pins)  # ranked = hybrid order BEFORE pins were added
            near_tie = bool(pinned) and candidate and (not others or pin_score >= max(others) - 0.06)
            if pin_first or near_tie:
                ranked = pins + [i for i in ranked if i not in pins]
            else:
                ranked = ranked[:1] + [i for i in pins if i not in ranked[:1]] + [i for i in ranked[1:] if i not in pins]
        ranked = ranked[:k]
        hits = [by_id[i] for i in ranked]
        for h in hits:
            h.score = scores.get(h.id, h.score)
        confidence = max((scores.get(i, 0.0) for i in ranked), default=0.0)
        return Retrieval(hits=hits, confidence=confidence, vec=vec)

    async def retrieve_from(self, sources: list[str], p: Principal, vec: list[float], per_source: int = 2,
                            prefer: list[str] | None = None) -> Retrieval:
        """DIRECT lookup: the best sections of specific documents only (no search over the whole knowledge base). Used when the
        question is already known to be about one named device: its guide is the answer, other pages only add tokens (and noise).
        `prefer` = section headings the question is about ("Troubleshooting"); those sections come first, then the best vector matches."""
        hits: list[Hit] = []
        top = 0.0
        for src in sources:
            cand = await self.store.vector_search(vec, p, limit=10, source=src)
            top = max([top, *(h.score for h in cand)])
            want = [h for name in (prefer or []) for h in cand if f"> {name}" in h.text[:160] or h.text.startswith(f"{name}")]
            picked = list({h.id: h for h in want + cand}.values())[:per_source]
            hits += picked
        return Retrieval(hits=hits, confidence=top, vec=vec)
