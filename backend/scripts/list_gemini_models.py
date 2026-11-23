"""List the Gemini models your key can call (to pick GEMINI_ANSWER_MODEL). Run: python -m scripts.list_gemini_models"""
import httpx

from app.config import Settings

s = Settings()
r = httpx.get("https://generativelanguage.googleapis.com/v1beta/models", headers={"x-goog-api-key": s.gemini_api_key},
              params={"pageSize": 200}, timeout=20)
r.raise_for_status()
for m in sorted(r.json().get("models", []), key=lambda m: m["name"]):
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m["name"].removeprefix("models/"))
