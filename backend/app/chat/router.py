"""L3 router: one small-model call -> intent + standalone query (+ reply for smalltalk)."""
import json
import logging
from dataclasses import dataclass
from enum import Enum

from app.config import Settings
from app.llm.llm_client import LLMClient, LLMError
from app.llm.prompts import ROUTER_SYSTEM
from app.rag.tokens import truncate_tokens

log = logging.getLogger("grovator.router")


class Intent(str, Enum):
    platform_faq = "platform_faq"
    my_data = "my_data"
    smalltalk = "smalltalk"
    off_topic = "off_topic"
    unsafe = "unsafe"


@dataclass
class Route:
    intent: Intent
    query: str
    reply: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    fallback: bool = False  # router failed; defaulted to platform_faq
    shortcut: bool = False  # decided by free rules, no LLM call


async def route_message(llm: LLMClient, s: Settings, summary: str, last_users: list[str] | str, message: str) -> Route:
    users = [last_users] if isinstance(last_users, str) else list(last_users)
    users = [u for u in users if u]
    last_user = users[-1] if users else ""
    ctx = f"SUMMARY: {truncate_tokens(summary, 120) or '(none)'}\n"
    if len(users) == 1:
        ctx += f"PREVIOUS USER MESSAGE: {truncate_tokens(users[0], 60)}\n"
    elif users:
        ctx += "PREVIOUS USER MESSAGES: " + " | ".join(truncate_tokens(u, 40) for u in users) + "\n"
    try:
        res = await llm.chat(
            model=s.router_model,
            messages=[{"role": "system", "content": ROUTER_SYSTEM},
                      {"role": "user", "content": f"{ctx}MESSAGE: {message}"}],
            max_tokens=250, json_mode=True, temperature=0,
        )
        data = json.loads(res.text)
        intent = Intent(data.get("intent", ""))
        query = str(data.get("query") or "").strip() or message
        return Route(intent, query[:300], str(data.get("reply") or "")[:200],
                     res.input_tokens, res.output_tokens)
    except (LLMError, ValueError, TypeError, AttributeError) as exc:
        # Fail open to retrieval only: the grounding gate still blocks anything unrelated.
        log.warning("router_failed %s", type(exc).__name__)
        # Without a rewrite, keep follow-ups searchable by prepending the previous question.
        query = f"{last_user} {message}".strip() if last_user else message
        return Route(Intent.platform_faq, query[:300], fallback=True)
