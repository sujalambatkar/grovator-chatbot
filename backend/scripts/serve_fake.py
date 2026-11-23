"""DEV/E2E ONLY: the real FastAPI app with a deterministic stand-in for Groq (no API key, no quota).
Answers are built from the retrieved CONTEXT so they are grounded and pass the output guard.
Run: uvicorn scripts.serve_fake:app --port 8000"""
import json
import re

from app.config import Settings
from app.llm import prompts as P
from app.llm.llm_client import LLMResult
from app.main import create_app
from app.rag.tokens import count_tokens

_DATA = re.compile(r"\b(marks?|scores?|reports?|progress|schedule|batch(?:es)?)\b", re.I)


class DevFakeLLM:
    def _res(self, text: str, messages) -> LLMResult:
        return LLMResult(text=text, model="dev-fake", latency_ms=1, output_tokens=count_tokens(text),
                         input_tokens=sum(count_tokens(m["content"]) for m in messages))

    async def chat(self, *, model, messages, max_tokens, json_mode=False, temperature=0.2, reasoning_effort=None):
        sys_, user = messages[0]["content"], messages[-1]["content"]
        msg = user.split("MESSAGE: ", 1)[-1].strip()
        if sys_ == P.ROUTER_SYSTEM:
            intent = "my_data" if _DATA.search(msg) else "platform_faq"
            return self._res(json.dumps({"intent": intent, "query": msg, "reply": ""}), messages)
        if sys_ == P.TOOL_PLAN_SYSTEM:
            if re.search(r"schedule", msg, re.I):
                plan = {"tool": "get_schedule", "args": {}}
            elif re.search(r"\bmy\b", msg, re.I):
                plan = {"tool": "get_student_report", "args": {"student": "me"}}
            else:
                name = re.search(r"\b([A-Z][a-z]{2,})\b(?!.*\b[A-Z][a-z]{2,}\b)", msg)
                plan = {"tool": "get_student_report", "args": {"student": name.group(1)}} if name else {"tool": "none"}
            return self._res(json.dumps(plan), messages)
        if sys_ == P.EXTRACT_SYSTEM:
            m = re.search(r"I (?:prefer|like) ([^.?!]{3,60})", user)
            mem = [{"text": f"Prefers {m.group(1).strip()}", "type": "preference"}] if m else []
            return self._res(json.dumps({"memories": mem}), messages)
        return self._res("Summary: " + user.split("USER: ", 1)[-1].splitlines()[0][:80], messages)

    async def answer(self, messages):
        return self._res(self._reply(messages), messages)

    async def answer_stream(self, messages):
        res = self._res(self._reply(messages), messages)
        yield "delta", res.text
        yield "done", res

    @staticmethod
    def _reply(messages) -> str:
        ctx = messages[-1]["content"].split("CONTEXT:\n", 1)[-1].split("\n\nQUESTION:", 1)[0]
        first = re.sub(r"^\[\d+\]\s*", "", ctx.splitlines()[0])
        body = first.split(": ", 1)[-1]
        sents = re.split(r"(?<=[.!?])\s+", body)
        return " ".join(sents[:3]).strip() or "I don't have that information right now."


app = create_app(llm=DevFakeLLM(), settings=Settings(usage_log_dir="", demo_mode=True, rate_limit_user_per_min=1000, rate_limit_ip_per_min=1000))
