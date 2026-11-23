"""Print Groq's live model list (key from .env) and check the configured model names."""
from groq import Groq

from app.config import get_settings

s = get_settings()
ids = sorted(m.id for m in Groq(api_key=s.groq_api_key).models.list().data)
print("\n".join(ids))
for name, val in [("ROUTER_MODEL", s.router_model), ("ANSWER_MODEL", s.answer_model), ("GUARD_MODEL", s.guard_model)]:
    print(f"{name}={val}: {'OK' if val in ids else 'NOT AVAILABLE'}")
