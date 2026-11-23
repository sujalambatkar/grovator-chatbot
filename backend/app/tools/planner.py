"""LLM proposes {tool, args}; code validates and scopes. The model never sees or chooses user/org ids."""
import json
from dataclasses import dataclass

from app.config import Settings
from app.llm.llm_client import LLMClient, LLMError
from app.llm.prompts import TOOL_PLAN_SYSTEM
from app.rag.tokens import truncate_tokens


@dataclass
class Plan:
    tool: str | None
    args: dict
    tokens: int = 0


async def plan_tool(llm: LLMClient, s: Settings, message: str, summary: str = "") -> Plan:
    try:
        res = await llm.chat(
            model=s.router_model,
            messages=[{"role": "system", "content": TOOL_PLAN_SYSTEM},
                      {"role": "user", "content": f"SUMMARY: {truncate_tokens(summary, 80) or '(none)'}\nMESSAGE: {message}"}],
            max_tokens=250, json_mode=True, temperature=0)
        data = json.loads(res.text)
        tool, args = str(data.get("tool", "none")), data.get("args", {})
        return Plan(None if tool == "none" else tool, args if isinstance(args, dict) else {}, res.input_tokens + res.output_tokens)
    except (LLMError, ValueError, AttributeError, TypeError):
        return Plan(None, {})
