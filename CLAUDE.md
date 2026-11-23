# CLAUDE.md

Guidance for Claude Code when working in this repo. Read `README.md` for the full picture; this file is the short, practical version.

## What this is
A guardrailed, token-efficient chatbot for **Grovator** (Grok Learning's ed-tech platform). A Next.js demo page shows course content (courses, lessons, block reference, platform tools); a chat widget answers questions about that content and the platform, remembers the conversation, refuses anything off-topic, and shows tokens used vs. a naive bot.

- `backend/` FastAPI + RAG (Qdrant, fastembed, Redis) + Groq LLM. Python 3.11.
- `frontend/` Next.js (App Router, TypeScript) demo page + `ChatWidget`. Node >= 18.18.
- `eval/` data-driven eval harness (JSONL test sets). `docs/chatbot/` decisions + bug diary.

**The knowledge base is the real Grovator ESP32 IoT Gateway dataset** (`backend/knowledge_src/hardware/*.json`, rendered into documents by `app/rag/hardware.py`; nothing is hardcoded per sensor). **The student data** (`app/tools/mock_adapter.py`) and the JWT claim shape are assumed or synthetic. The old dummy courses/lessons/blocks are only a test fixture (`backend/tests/fixtures/knowledge_synthetic/`); the mechanics suites and `eval --corpus synthetic` run on it, `tests/test_hardware.py` and the default eval run on the real dataset. Do not present the synthetic parts (`app/tools/mock_adapter.py`), the JWT claim shape. Do not present them as real Grovator data.

## Setup (first time)
```bash
cd backend
uv venv --python 3.11 .venv && uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env     # user must add GROQ_API_KEY (or GEMINI_API_KEY with LLM_PROVIDER=gemini) themselves; set DEMO_MODE=true, QDRANT_URL=memory, REDIS_URL=memory
cd ../frontend && cp .env.example .env.local && npm install   # set NEXT_PUBLIC_CHATBOT_API_URL to the backend URL
```
Windows: use `.venv\Scripts\python` instead of `.venv/bin/python`.

## Run
```bash
cd backend && .venv/bin/python -m uvicorn app.main:app --port 8001   # port 8000 is often taken
cd frontend && npm run dev                                           # http://localhost:3000
```
No Groq key? `uvicorn scripts.serve_fake:app --port 8765` runs the real app with a deterministic fake LLM (dev/E2E only).

## Test (run these before claiming anything works)
```bash
cd backend  && .venv/bin/python -m pytest -q                      # ~479 tests, no network/LLM needed
cd backend  && .venv/bin/python ../eval/run_eval.py --mode offline  # deterministic security + retrieval gates
cd frontend && npx tsc --noEmit && npx vitest run                 # typecheck + ~20 unit tests
# browser E2E (needs the fake backend on :8765; rebuild first):
cd frontend && NEXT_PUBLIC_CHATBOT_API_URL=http://localhost:8765 npx next build && node e2e/run.mjs
# live (uses real Groq quota; free tier is 8,000 tokens/min/model, so these are paced):
cd backend  && .venv/bin/python ../eval/run_eval.py --mode live
cd backend  && .venv/bin/python -m scripts.demo_compare           # measured token comparison
```

## Where things live (backend/app)
`chat/` pipeline (`service.py` is the entry), router shortcut, semantic cache, prompt building · `guardrails/` input rules, output guard, PII · `rag/` chunking, hybrid retriever, compression · `memory/` Redis sessions + long-term memory · `tools/` read-only scoped tools + policy · `content/` page catalog · `core/` auth, rate limits, budgets, usage · `api/` routes · `llm/` Groq client + all prompts (`prompts.py`).

## Hardware conversation (gateway dataset)
- `rag/hardware.py` generates docs (device guide, interface, wiring rules, concepts) plus an alias index; `chat/service.py` keeps the sensor in focus (`SessionState.focus`) so "it / what next / where does VCC go" find the right page, asks a clarifying question when "the sensor" is ambiguous, and `guardrails/output.unsupported_pins` blocks any GPIO number not present in the retrieved records (one strict retry first).
- Token path for a known sensor (D81-D85): direct lookup of 2 sections of that device's guide, no chat history, question rewritten with the device name, answer reusable across students. Section headings in `render_device` are matched by `_SECTION_INTENTS` in `chat/service.py`: rename both together.
- The dataset has no software/code/expected-output/troubleshooting facts: each device guide says so explicitly, and the bot must say "not documented" rather than invent. Do not add such facts unless they come from real Grovator documentation.

## Rules that matter (learned the hard way, see `docs/chatbot/BUGS.md`)
- **Never commit secrets.** `.env*` is gitignored except `.env.example`. Never put the Groq key in the frontend or in any tracked file. Never log raw user messages.
- **Security is enforced in code, not prompts.** Access scope comes only from the JWT (`core/auth.py`) via retrieval filters and `tools/policy.py`; the LLM never chooses whose data to fetch. Keep it that way.
- **Verify-then-stream**: the answer is checked by the output guard *before* any token is sent. Do not stream raw model output.
- **Tests pin their own env** in `tests/conftest.py` (optimisation flags off, `DEMO_MODE=false`, ...). Scripts in `backend/scripts/` must NOT import `tests.conftest` (it would silently turn optimisations off).
- **Prompt budgets are asserted by tests**: answer prompt <= 200 tokens, router prompt <= 150. Check with `count_tokens` after editing `llm/prompts.py`.
- **Prove a regression test can fail**: when you fix a bug, write the test first, watch it fail, then fix. For safety-critical code, mutate the code (disable the rule) and confirm tests/eval fail. Several earlier "passing" tests were vacuous.
- Retrieval thresholds (`MIN_RETRIEVAL_SCORE`, `GROUNDING_MIN_SUPPORT`, `compress_*`) were calibrated on the small synthetic corpus. Re-calibrate with measurements before changing them; do not guess.
- When adding knowledge docs, set front-matter `title`, `category`, `order`, `role_visibility`. Teacher/admin docs must not list `student`.
- **(Legacy fixture only)** Lesson order is content. If you add, move or rename a lesson, update `courses/learning-path.md` and the `Previous lesson:` / `Next lesson:` lines on its neighbours; `test_every_lesson_page_states_its_previous_and_next_lesson...` fails if they disagree with the `order` front-matter.
- Be honest in reports: say what was and was not run (live Groq vs stubbed), and keep known limitations in `docs/chatbot/BUGS.md`.

## Gotchas
- Local Qdrant/Redis default to in-memory (`memory`): sessions, memories and cache reset on restart.
- `NEXT_PUBLIC_*` is baked in at build time; rebuild the frontend after changing the API URL.
- Node's `fetch("localhost")` can prefer IPv6 while uvicorn binds IPv4; use `127.0.0.1` in Node-side scripts.
- `LLM_PROVIDER` picks the client (`llm/llm_client.py: make_llm`): Groq SDK or Gemini REST. Use `make_llm(settings)`, never `GroqLLMClient` directly. Gemini models come from `GEMINI_*_MODEL` (the validator copies them into `answer_model`/`router_model`). The Gemini key goes in a header only.
- `gpt-oss` models spend hidden reasoning tokens inside `max_tokens`; `reasoning_effort` is kept at `low`.
- Production mode (`ENVIRONMENT=production`) refuses to start with the default JWT secret, mock adapter, in-memory stores, `DEMO_MODE`, or localhost CORS. That is intentional.
