"""Rate limits (per user, per IP) and daily token budgets (per user, per org), all in Redis."""
import time
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from app.config import Settings
from app.core.auth import Principal, get_principal


class RateLimiter:
    """Fixed-window counter. Simple and atomic (INCR); allows a burst of up to 2x at a window boundary."""

    def __init__(self, redis, window_s: int = 60) -> None:
        self.r, self.window = redis, window_s

    async def hit(self, key: str, limit: int) -> tuple[bool, int]:
        now = time.time()
        k = f"govator:rl:{key}:{int(now // self.window)}"
        n = await self.r.incr(k)
        if n == 1:
            await self.r.expire(k, self.window + 5)
        return n <= limit, max(1, self.window - int(now) % self.window)


class TokenBudget:
    """Counts every LLM token (input + output, including hidden reasoning) per UTC day."""

    def __init__(self, redis, user_limit: int, org_limit: int) -> None:
        self.r, self.user_limit, self.org_limit = redis, user_limit, org_limit

    @staticmethod
    def _keys(p: Principal) -> tuple[str, str]:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"govator:tok:u:{p.org_id}:{p.user_id}:{day}", f"govator:tok:o:{p.org_id}:{day}"

    async def usage(self, p: Principal) -> tuple[int, int]:
        uk, ok = self._keys(p)
        u, o = await self.r.get(uk), await self.r.get(ok)
        return int(u or 0), int(o or 0)

    async def exceeded(self, p: Principal) -> bool:
        u, o = await self.usage(p)
        return u >= self.user_limit or o >= self.org_limit

    async def record(self, p: Principal, tokens: int) -> None:
        if tokens <= 0:
            return
        for k in self._keys(p):
            await self.r.incrby(k, tokens)
            await self.r.expire(k, 172800)


def client_ip(request: Request, settings: Settings) -> str:
    if settings.trust_proxy and (xff := request.headers.get("x-forwarded-for")):
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def limited_principal(request: Request, p: Principal = Depends(get_principal)) -> Principal:
    """Auth + per-IP + per-user rate limit. 429 carries Retry-After. Uses the settings the app was built with."""
    settings: Settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.limiter
    for key, limit in ((f"ip:{client_ip(request, settings)}", settings.rate_limit_ip_per_min),
                       (f"user:{p.org_id}:{p.user_id}", settings.rate_limit_user_per_min)):
        allowed, retry = await limiter.hit(key, limit)
        if not allowed:
            raise HTTPException(status_code=429, detail="Too many requests. Please slow down.",
                                headers={"Retry-After": str(retry)})
    return p
