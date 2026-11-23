# Grovator AI Assistant: Phase 0 Discovery (standalone mode)

**Status:** No Grovator codebase is available. Nothing here was verified against real code.
Every item is tagged **[GIVEN]** (stated in the project brief) or **[ASSUMED]** (my design default).
The chatbot is built as a standalone service behind **adapter interfaces**. When the real
platform details are known, only the adapters change; the pipeline stays the same.

## 1. Platform facts

| Area | Finding | Tag |
|---|---|---|
| Company / product | Grovator, edtech platform by Grok Learning Pvt Ltd | GIVEN |
| Likely DB | MongoDB (possibly PostgreSQL) | GIVEN (unverified) |
| Auth | JWT-based RBAC | GIVEN (unverified) |
| Roles | superadmin / admin / teacher, maybe student | GIVEN (unverified) |
| Hierarchy | org > lab > school > batch > student | GIVEN (unverified) |
| Subjects | IoT, Robotics Foundation (others unknown) | GIVEN |
| Features to answer about | courses, labs, reports, schedules | GIVEN |
| Backend / frontend | FastAPI / Next.js App Router | GIVEN |
| Deployment | Render / Vercel / Upstash / Qdrant Cloud free tiers | ASSUMED |

## 2. Assumed JWT claims (the contract the bot needs)

```json
{ "sub": "user_id", "role": "teacher", "org_id": "...", "lab_id": "...",
  "school_id": "...", "batch_ids": ["..."], "exp": 0 }
```
- The bot verifies the token with a shared secret or JWKS (`JWT_SECRET` / `JWT_JWKS_URL`).
- Scope is derived **only** from these claims, never from the message text or the LLM.
- Assumed: `batch_ids` are carried in the token.

## 3. Scope matrix (enforced in code)

| Role | Data visible |
|---|---|
| superadmin | everything in their `org_id` |
| admin | their `school_id` |
| teacher | their `batch_ids` and the students in them |
| student (if it exists) | self only |

## 4. Knowledge base sources (to ingest in Phase 1)

I don't have the real content. Phase 1 will ship a loader that reads Markdown/PDF/HTML/JSON files
from `knowledge_src/`, with front-matter metadata (`role_visibility`, `org_id`, `school_id`, `source`).
Planned categories:
1. Platform how-to / user guides, per role
2. FAQs
3. Course and curriculum content (IoT, Robotics Foundation, others)
4. Lab setup and troubleshooting help
5. Report documentation (what each report contains, how to read it, field definitions)
6. Schedule / timetable help

Phase 1 tests need 15 Q&A pairs. **I will write a clearly labelled sample corpus** (synthetic, not real
Grovator content), so retrieval can be tested. Replace it with real docs before production.

## 5. Candidate read-only tools (Phase 4)

These are **hypothetical interfaces**, implemented behind a `PlatformDataAdapter` protocol.
In dev they are backed by a mock in-memory dataset.

| Tool | Args | Returns |
|---|---|---|
| `list_my_batches` | none | batches in scope |
| `get_batch_summary` | `batch_id` | size, subject, schedule |
| `get_student_report` | `student_id`, `subject?` | scores/progress (scope-checked) |
| `get_schedule` | `batch_id`, `date_range?` | sessions |
| `get_lab_info` | `lab_id` | lab details |

Rules: Pydantic-validated args; the `batch_id`/`student_id` is checked against JWT scope in code; no writes.

## 6. Repo layout (proposed, standalone)

```
backend/   FastAPI app (app/{api,core,llm,rag,memory,guardrails,tools,adapters}), tests/
frontend/  Next.js widget (Phase 6)
eval/      eval harness (Phase 6)
docs/chatbot/  DISCOVERY.md, DECISIONS.md, BUGS.md
```
