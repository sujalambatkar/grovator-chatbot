"""Observability: per-request JSONL log + Redis aggregates behind /admin/chatbot/usage.

PRIVACY: the record is built from a fixed whitelist. There is no field that can hold message text, and the user
is stored only as a short salted-free hash of org:user (enough to count distinct users, not to identify them).
"""
import asyncio
import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.auth import Principal

ALLOWED = ("request_id", "intent", "cache_hit", "shortcut", "input_tokens", "output_tokens", "router_tokens", "model",
           "latency_ms", "refused_reason", "memories_used", "truncated", "tool", "fallback", "canned")
COMPONENTS = ("router", "planner", "answer_in", "answer_out", "summary", "extract")


def _day(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=offset)).strftime("%Y%m%d")


def user_hash(p: Principal) -> str:
    return hashlib.sha256(f"{p.org_id}:{p.user_id}".encode()).hexdigest()[:12]


def _pct(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * (len(xs) - 1) + 0.5))]


class UsageTracker:
    def __init__(self, redis, log_dir: str = "") -> None:
        self.r, self.log_dir, self._lock = redis, log_dir, threading.Lock()

    @staticmethod
    def _key(p_org: str, school: str | None, day: str, kind: str = "agg") -> str:
        return f"grovator:usage:{kind}:{p_org}:{school or '-'}:{day}"

    async def tokens(self, p: Principal, component: str, n: int) -> None:
        if n > 0 and component in COMPONENTS:
            await self.r.hincrby(self._key(p.org_id, p.school_id, _day()), f"tok_{component}", n)

    async def remember_request(self, p: Principal, rid: str, rec: dict) -> None:
        """Short-lived link request_id -> (user, scope, intent) so feedback can be validated. No text stored."""
        await self.r.set(f"grovator:req:{rid}", json.dumps({
            "user": user_hash(p), "org": p.org_id, "school": p.school_id,
            "intent": rec.get("intent", "unknown"), "cache_hit": bool(rec.get("cache_hit"))}), ex=86400)

    async def feedback(self, p: Principal, rid: str, rating: str, reason: str | None) -> bool:
        """Record one rating per request (changeable). Only the user who made the request may rate it."""
        raw = await self.r.get(f"grovator:req:{rid}")
        if not raw:
            return False
        meta = json.loads(raw)
        if meta["user"] != user_hash(p) or meta["org"] != p.org_id:
            return False  # someone else's request: indistinguishable from "not found"
        fb_key, why_key = f"grovator:fb:{rid}", f"grovator:fbr:{rid}"
        old, old_reason = await self.r.get(fb_key), await self.r.get(why_key)
        new_reason = reason if rating == "down" else None
        if old == rating and (new_reason is None or new_reason == old_reason):
            return True  # nothing new. (A reason arriving AFTER the thumbs-down is new information: handled below.)
        key = self._key(meta["org"], meta["school"], _day())
        pipe = self.r.pipeline()
        if old != rating:
            if old in ("up", "down"):
                pipe.hincrby(key, f"fb_{old}", -1)
                pipe.hincrby(key, f"fb_{old}_{meta['intent']}", -1)
            pipe.hincrby(key, f"fb_{rating}", 1)
            pipe.hincrby(key, f"fb_{rating}_{meta['intent']}", 1)
            pipe.set(fb_key, rating, ex=86400 * 40)
        if old_reason and (old != rating or new_reason != old_reason):  # retract the previous reason
            pipe.hincrby(key, f"fbreason_{old_reason}", -1)
            pipe.delete(why_key)
        if new_reason and new_reason != old_reason or (new_reason and old != rating):
            pipe.hincrby(key, f"fbreason_{new_reason}", 1)
            pipe.set(why_key, new_reason, ex=86400 * 40)
        pipe.expire(key, 40 * 86400)
        await pipe.execute()
        if self.log_dir:  # feedback JSONL: random request id, intent, rating, fixed reason. No user, org or text.
            line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "request_id": rid,
                               "intent": meta.get("intent", "unknown"), "rating": rating, "reason": new_reason})
            await asyncio.to_thread(self._append, line, "feedback")
        return True

    async def request(self, p: Principal, rec: dict) -> dict:
        rec = {k: rec[k] for k in ALLOWED if k in rec}
        key = self._key(p.org_id, p.school_id, _day())
        pipe = self.r.pipeline()
        pipe.hincrby(key, "requests", 1)
        pipe.hincrby(key, f"intent_{rec.get('intent', 'unknown')}", 1)
        if rec.get("cache_hit"):
            pipe.hincrby(key, "cache_hits", 1)
        if rec.get("shortcut"):
            pipe.hincrby(key, "shortcuts", 1)
        if rec.get("refused_reason"):
            pipe.hincrby(key, f"refused_{rec['refused_reason']}", 1)
            pipe.hincrby(key, "refused_total", 1)
        pipe.expire(key, 40 * 86400)
        lat = self._key(p.org_id, p.school_id, _day(), "lat")
        pipe.lpush(lat, int(rec.get("latency_ms", 0)))
        pipe.ltrim(lat, 0, 4999)
        pipe.expire(lat, 40 * 86400)
        await pipe.execute()
        if self.log_dir:
            line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                               "role": p.role.value, "org": p.org_id, "school": p.school_id,
                               "user": user_hash(p), **rec})
            await asyncio.to_thread(self._append, line)
        return rec

    def _append(self, line: str, kind: str = "usage") -> None:
        d = Path(self.log_dir)
        d.mkdir(parents=True, exist_ok=True)
        with self._lock, open(d / f"{kind}-{_day()}.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")

    async def summary(self, org_id: str, school_id: str | None, days: int) -> dict:
        """Aggregate the last `days` UTC days for one org (all schools) or one school."""
        totals: dict[str, int] = {}
        lat: list[float] = []
        per_day = []
        for off in range(days):
            day, day_total = _day(off), {"requests": 0, "tokens": 0}
            pattern = self._key(org_id, school_id or "*", day)
            async for key in self.r.scan_iter(match=pattern):
                for f, v in (await self.r.hgetall(key)).items():
                    totals[f] = totals.get(f, 0) + int(v)
                    if f == "requests":
                        day_total["requests"] += int(v)
                    if f.startswith("tok_"):
                        day_total["tokens"] += int(v)
            async for key in self.r.scan_iter(match=self._key(org_id, school_id or "*", day, "lat")):
                lat += [float(x) for x in await self.r.lrange(key, 0, -1)]
            per_day.append({"day": day, **day_total})
        req = totals.get("requests", 0)
        tok = {c: totals.get(f"tok_{c}", 0) for c in COMPONENTS}
        total_tokens = sum(tok.values())
        return {
            "days": days, "scope": {"org_id": org_id, "school_id": school_id}, "requests": req,
            "intents": {k[7:]: v for k, v in totals.items() if k.startswith("intent_")},
            "cache_hit_rate": round(totals.get("cache_hits", 0) / req, 4) if req else 0.0,
            "router_shortcut_rate": round(totals.get("shortcuts", 0) / req, 4) if req else 0.0,
            "refused": {k[8:]: v for k, v in totals.items() if k.startswith("refused_") and k != "refused_total"},
            "tokens": {**tok, "total": total_tokens},
            "avg_tokens_per_request": round(total_tokens / req, 1) if req else 0.0,
            "avg_answer_input_tokens": round(tok["answer_in"] / max(1, totals.get("intent_platform_faq", 0) - totals.get("cache_hits", 0)), 1),
            "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "samples": len(lat)},
            "feedback": {
                "up": totals.get("fb_up", 0), "down": totals.get("fb_down", 0),
                "satisfaction": round(totals.get("fb_up", 0) / (totals.get("fb_up", 0) + totals.get("fb_down", 0)), 3)
                if totals.get("fb_up", 0) + totals.get("fb_down", 0) else None,
                "down_reasons": {k[9:]: v for k, v in totals.items() if k.startswith("fbreason_")},
            },
            "per_day": per_day,
        }
