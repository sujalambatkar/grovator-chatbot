"""L2 semantic cache (Redis). A hit costs 0 LLM tokens.

Safety rules (each one is enforced by a test):
  * scoped by knowledge version + role + org + school: a student's entry can never be served to a teacher
    or to another tenant, and re-ingesting the knowledge base invalidates every entry;
  * only plain knowledge answers are stored: never `my_data`, smalltalk or refusals, and never an answer that
    was shaped by user memory, a conversation summary or earlier turns (those could carry one user's context);
  * never an answer that was redacted or contains a URL.
"""
import base64
import json
import re
import time
import uuid
from dataclasses import dataclass

import numpy as np

from app.config import Settings
from app.core.auth import Principal

_URL = re.compile(r"https?://|www\.|\.(?:com|net|org|in|io)\b", re.I)
_PLACEHOLDER = re.compile(r"\[(?:ID|EMAIL|PHONE|AADHAAR|JWT|API_KEY|CREDENTIAL)\]")


@dataclass
class CacheHit:
    answer: str
    sources: list[str]
    score: float
    question: str


def cacheable(message: str, answer: str) -> bool:
    return len(message) <= 200 and not _URL.search(answer) and not _PLACEHOLDER.search(answer)


def _enc(vec: list[float]) -> str:
    return base64.b64encode(np.asarray(vec, dtype=np.float32).tobytes()).decode()


def _dec(s: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(s), dtype=np.float32)


class SemanticCache:
    def __init__(self, redis, settings: Settings) -> None:
        self.r, self.s = redis, settings

    @staticmethod
    def _key(p: Principal, kb_version: str) -> str:
        return f"grovator:cache:{kb_version}:{p.role.value}:{p.org_id}:{p.school_id or '*'}"

    async def lookup(self, p: Principal, vec: list[float], kb_version: str) -> CacheHit | None:
        raw = await self.r.hgetall(self._key(p, kb_version))
        if not raw:
            return None
        q = np.asarray(vec, dtype=np.float32)
        qn = float(np.linalg.norm(q)) or 1.0
        best, best_score = None, 0.0
        for blob in raw.values():
            e = json.loads(blob)
            v = _dec(e["v"])
            score = float(np.dot(q, v) / (qn * (float(np.linalg.norm(v)) or 1.0)))
            if score > best_score:
                best, best_score = e, score
        if best is None or best_score < self.s.cache_threshold:
            return None
        return CacheHit(best["a"], best["src"], best_score, best["q"])

    async def store(self, p: Principal, vec: list[float], question: str, answer: str,
                    sources: list[str], kb_version: str) -> bool:
        key = self._key(p, kb_version)
        if await self.lookup(p, vec, kb_version) is not None:  # near-duplicate already cached (>= threshold)
            return False
        if await self.r.hlen(key) >= self.s.cache_max_per_scope:
            entries = {k: json.loads(v)["ts"] for k, v in (await self.r.hgetall(key)).items()}
            await self.r.hdel(key, min(entries, key=entries.get))  # evict oldest
        await self.r.hset(key, uuid.uuid4().hex, json.dumps(
            {"q": question[:200], "a": answer, "src": sources, "v": _enc(vec), "ts": time.time()}))
        await self.r.expire(key, self.s.cache_ttl_s)
        return True
