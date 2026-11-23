"""Short-term memory: rolling summary + last 2 raw turns, in Redis, keyed by (user_id, session_id).
Including user_id in the key means a guessed session_id can never expose another user's context."""
import json
import re
from dataclasses import asdict, dataclass, field

SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
MAX_TURNS = 2


@dataclass
class Turn:
    user: str
    assistant: str


@dataclass
class SessionState:
    summary: str = ""
    turns: list[Turn] = field(default_factory=list)
    history_tokens: int = 0  # tokens of every completed turn: what a 'send the full chat every time' bot would resend


def make_redis(url: str):
    if url == "memory":
        import fakeredis.aioredis

        return fakeredis.aioredis.FakeRedis(decode_responses=True)
    import redis.asyncio as redis

    return redis.from_url(url, decode_responses=True)


class SessionStore:
    def __init__(self, redis, ttl_s: int) -> None:
        self.r, self.ttl = redis, ttl_s

    @staticmethod
    def _key(user_id: str, session_id: str) -> str:
        return f"grovator:sess:{user_id}:{session_id}"

    async def get(self, user_id: str, session_id: str) -> SessionState:
        raw = await self.r.get(self._key(user_id, session_id))
        if not raw:
            return SessionState()
        d = json.loads(raw)
        return SessionState(d.get("summary", ""), [Turn(**t) for t in d.get("turns", [])], int(d.get("history_tokens", 0)))

    async def save(self, user_id: str, session_id: str, state: SessionState) -> None:
        state.turns = state.turns[-MAX_TURNS:]
        await self.r.set(self._key(user_id, session_id), json.dumps(asdict(state)), ex=self.ttl)

    async def delete(self, user_id: str, session_id: str) -> None:
        await self.r.delete(self._key(user_id, session_id))


_PREFIX = "Earlier questions: "


def fold_summary(summary: str, user_msg: str, max_tokens: int) -> str:
    """Zero-token rolling summary: the list of what the user asked earlier, oldest dropped first.
    (The answers are not kept: they can be re-retrieved from the knowledge base if the topic comes back.)"""
    from app.rag.tokens import count_tokens, truncate_tokens

    items = [x for x in summary.removeprefix(_PREFIX).split(" | ") if x]
    items.append(truncate_tokens(" ".join(user_msg.split()), 30))
    while len(items) > 1 and count_tokens(_PREFIX + " | ".join(items)) > max_tokens:
        items.pop(0)
    return _PREFIX + " | ".join(items)
