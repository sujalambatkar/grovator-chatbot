"""L5 context compression: sentence-level selection by similarity to the query, then a hard token budget.

For each retrieved chunk we keep only the sentences that score close to the chunk's best sentence (cosine of the
local bge embeddings, free), drop chunks that are only weakly related (the top chunk is always kept), and finally
enforce the token budget. Sentence vectors are cached because the knowledge base is small and static.
"""
import numpy as np

from app.config import Settings
from app.rag.chunking import _sentences
from app.rag.embeddings import embed_docs
from app.rag.store import Hit
from app.rag.tokens import count_tokens

_VEC_CACHE: dict[str, np.ndarray] = {}
_VEC_CACHE_MAX = 20_000


def _split(hit: Hit) -> tuple[str, list[str]]:
    """chunk text = '<title> > <heading>: <sentences>' -> (prefix, sentences)."""
    i = hit.text.find(": ", len(hit.title))
    if i < 0:
        return "", _sentences(hit.text)
    return hit.text[: i + 2], _sentences(hit.text[i + 2 :])


async def _vectors(sentences: list[str]) -> list[np.ndarray]:
    missing = [s for s in dict.fromkeys(sentences) if s not in _VEC_CACHE]
    if missing:
        if len(_VEC_CACHE) + len(missing) > _VEC_CACHE_MAX:
            _VEC_CACHE.clear()
        for s, v in zip(missing, await embed_docs(missing)):
            _VEC_CACHE[s] = np.asarray(v, dtype=np.float32)
    return [_VEC_CACHE[s] for s in sentences]


async def compress_hits(hits: list[Hit], qvec: list[float] | list[list[float]], s: Settings) -> list[Hit]:
    """`qvec` may be one query vector or several (e.g. previous-question+message AND the new message alone):
    a sentence is kept if it is relevant to ANY of them, so a topic shift in a follow-up is not compressed away."""
    if not hits:
        return []
    qs = [np.asarray(v, dtype=np.float32) for v in (qvec if qvec and isinstance(qvec[0], (list, tuple)) else [qvec])]
    qs = [q / (float(np.linalg.norm(q)) or 1.0) for q in qs]
    split = [_split(h) for h in hits]
    vecs = await _vectors([x for _, sents in split for x in sents])
    out: list[Hit] = []
    pos = 0
    for rank, (h, (prefix, sents)) in enumerate(zip(hits, split)):
        v = vecs[pos : pos + len(sents)]
        pos += len(sents)
        if not sents:
            out.append(h)
            continue
        scores = [max(float(np.dot(q, x) / (float(np.linalg.norm(x)) or 1.0)) for q in qs) for x in v]
        best = max(scores)
        if rank > 0 and best < s.compress_chunk_min:
            continue  # weakly related chunk: drop entirely
        cores = [i for i in sorted(range(len(sents)), key=lambda i: -scores[i])
                 if scores[i] >= best * s.compress_keep_ratio][: s.compress_max_sents]
        # sentence window: a relevant sentence usually needs its neighbours (the example, the next step)
        keep = {j for i in cores for j in range(i - s.compress_window, i + s.compress_window + 1) if 0 <= j < len(sents)}
        text = prefix + " ".join(sents[i] for i in sorted(keep))
        out.append(Hit(id=h.id, text=text, title=h.title, source=h.source, summary=h.summary, score=h.score))
    return out


def build_context(hits: list[Hit], budget: int) -> tuple[str, list[Hit]]:
    parts: list[str] = []
    used: list[Hit] = []
    total = 0
    for h in hits:
        t = count_tokens(h.text)
        if total + t > budget:
            # fall back to the 1-line summary if it fits, else stop
            sm = count_tokens(h.summary)
            if total + sm <= budget:
                parts.append(f"[{len(used)+1}] {h.summary}")
                used.append(h)
                total += sm
            break
        parts.append(f"[{len(used)+1}] {h.text}")
        used.append(h)
        total += t
    return "\n".join(parts), used
