"""Chat pipeline. One async event generator feeds both /chat and /chat/stream.

L1 input guardrails -> budget -> L2 semantic cache -> L3 router (or free shortcut) ->
(my_data: scoped read-only tools | faq: L4 retrieval + memory -> L5 compression -> L6 answer) ->
L7 output guardrails -> L8 post-processing (turn, lazy summary, memory, cache store, usage log).
"""
import asyncio
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.chat.cache import SemanticCache, cacheable
from app.chat.prompting import build_answer_messages
from app.chat.router import Intent, route_message
from app.chat.shortcut import DEICTIC, try_shortcut
from app.content.catalog import ContentCatalog, Doc
from app.config import Settings
from app.core.abuse import TokenBudget
from app.core.auth import Principal
from app.core.usage import UsageTracker
from app.guardrails.input import check_input
from app.guardrails.output import check_output, cited_sources
from app.llm.llm_client import LLMClient, LLMError
from app.llm.prompts import (
    ANSWER_SYSTEM, BUDGET_REPLY, MY_DATA_REPLY, NO_INFO_REPLY, OFF_TOPIC_REPLY, REDIRECT_REPLY, SMALLTALK_FALLBACK, SUMMARY_SYSTEM, UNSAFE_REPLY,
)
from app.memory.long_term import MemoryManager
from app.memory.session import MAX_TURNS, SessionState, SessionStore, Turn, fold_summary
from app.rag.compress import compress_hits
from app.rag.embeddings import embed_query
from app.rag.retriever import HybridRetriever
from app.rag.tokens import count_tokens, truncate_tokens
from app.tools.adapter import PlatformDataAdapter
from app.tools.executor import run_tool
from app.tools.planner import plan_tool
from app.tools.types import Scope

log = logging.getLogger("govator.chat")  # NEVER log raw user text

Event = dict[str, Any]



def _chunks(text: str, words: int = 3) -> list[str]:
    """Re-chunk verified text so the client still gets a typing effect."""
    parts = re.findall(r"\S+\s*", text)
    return ["".join(parts[i : i + words]) for i in range(0, len(parts), words)]


TOOL_HELP = ("I can look up a student's report, your batches or your schedule. "
             "Tell me which one, for example: \"Show Aarav's report\".")


class ChatService:
    def __init__(
        self, llm: LLMClient, retriever: HybridRetriever, sessions: SessionStore, settings: Settings,
        memory: MemoryManager | None = None, budget: TokenBudget | None = None,
        adapter: PlatformDataAdapter | None = None, cache: SemanticCache | None = None,
        tracker: UsageTracker | None = None, catalog: ContentCatalog | None = None,
    ) -> None:
        self.llm, self.retriever, self.sessions, self.s = llm, retriever, sessions, settings
        self.memory, self.budget, self.adapter, self.cache, self.tracker = memory, budget, adapter, cache, tracker
        self.catalog = catalog
        if memory is not None:
            memory.on_usage = lambda p, n: self._record(p, n, "extract")
        self._pending: set[asyncio.Task] = set()

    async def drain(self) -> None:
        """Wait for background post-processing (used by tests and on shutdown)."""
        if self._pending:
            await asyncio.gather(*self._pending, return_exceptions=True)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _record(self, p: Principal, tokens: int, component: str = "other") -> None:
        if self.budget:
            await self.budget.record(p, tokens)
        if self.tracker:
            await self.tracker.tokens(p, component, tokens)

    @staticmethod
    def _done(base: dict, text: str, **extra: Any) -> Event:
        extra.setdefault("tokens_total", base.get("router_tokens", 0) + extra.get("input_tokens", 0) + extra.get("output_tokens", 0))
        return {"type": "done", "answer": text, "naive_tokens": 0, **base, "sources": [], **extra}

    # ------------------------------------------------------------------ entry point (adds timing + usage log)
    async def events(self, p: Principal, message: str, session_id: str | None, page: str | None = None) -> AsyncIterator[Event]:
        t0 = time.perf_counter()
        rid = uuid.uuid4().hex
        async for ev in self._run(p, message, session_id, page):
            if ev["type"] in ("done", "error"):
                ev["latency_ms"] = int((time.perf_counter() - t0) * 1000)
                ev["request_id"] = rid
                if self.tracker:
                    rec = dict(ev) if ev["type"] == "done" else {"intent": "error", "refused_reason": "llm_error",
                                                                  "latency_ms": ev["latency_ms"]}
                    try:
                        rec["request_id"] = rid
                        await self.tracker.request(p, rec)
                        await self.tracker.remember_request(p, rid, rec)
                    except Exception as exc:  # noqa: BLE001 - observability must never break a chat
                        log.warning("usage_log_failed %s", type(exc).__name__)
            yield ev

    async def _run(self, p: Principal, message: str, session_id: str | None, page: str | None = None) -> AsyncIterator[Event]:
        sid = session_id or uuid.uuid4().hex
        base: dict[str, Any] = {"session_id": sid, "intent": "blocked", "router_tokens": 0}

        # L1: deterministic input guardrails. Blocked messages cost 0 LLM tokens and touch no memory.
        verdict = check_input(message, p.role, self.s.max_message_chars)
        if not verdict.allowed:
            yield {"type": "meta", **base}
            yield self._done(base, verdict.reply or UNSAFE_REPLY, grounded=False, refused_reason=verdict.reason)
            return
        message = verdict.clean  # PII-redacted: the only form that reaches an LLM, memory, cache or logs

        if self.budget and await self.budget.exceeded(p):
            yield {"type": "meta", **base, "intent": "budget"}
            yield self._done(base, BUDGET_REPLY, grounded=False, refused_reason="budget_exceeded")
            return

        state = await self.sessions.get(p.user_id, sid)
        had_history = bool(state.turns or state.summary)
        page_doc = self.catalog.get(p, page) if (self.catalog and page) else None
        use_cache = self.cache is not None and self.s.enable_semantic_cache
        # The page the user is reading (only if they may see it; unknown and hidden pages are treated the same).
        naive0 = self._naive(p, page_doc, state, message)  # what a bot without retrieval would send for this message
        version = f"{self.retriever.version}:{page_doc.source if page_doc else '-'}"  # answers are page-specific
        qvec: list[float] | None = None
        checked_vec_for: str | None = None

        # L2a: a first message is standalone by definition, so it can be matched before the router runs.
        pre_text = f"{page_doc.title}: {message}" if (page_doc and DEICTIC.search(message)) else message
        if use_cache and not had_history:
            qvec = await embed_query(pre_text)
            hit = await self.cache.lookup(p, qvec, version)
            checked_vec_for = message
            if hit:
                base = {**base, "intent": Intent.platform_faq.value}
                yield {"type": "meta", **base}
                async for ev in self._serve_cached(p, sid, state, message, hit, "pre_router", base, page_doc):
                    yield ev
                return

        # L3: free shortcut for obvious cases, otherwise one small-model call.
        route = (try_shortcut(message, bool(state.turns), state.turns[-1].user if state.turns else "", on_page=page_doc is not None)
                 if self.s.enable_router_shortcut else None)
        if route is None:
            recent = [t.user for t in state.turns[-2:]] if self.s.lazy_summary else [t.user for t in state.turns[-1:]]
            route = await route_message(self.llm, self.s, state.summary, recent, message)
            await self._record(p, route.input_tokens + route.output_tokens, "router")
        base = {"session_id": sid, "intent": route.intent.value, "router_tokens": route.input_tokens + route.output_tokens,
                "shortcut": route.shortcut, "naive_tokens": naive0}
        yield {"type": "meta", **base}

        canned = {Intent.unsafe: (UNSAFE_REPLY, "unsafe"), Intent.off_topic: (OFF_TOPIC_REPLY, "off_topic")}
        if route.intent in canned:
            text, reason = canned[route.intent]
            yield self._done(base, text, grounded=False, refused_reason=reason)
            return
        if route.intent is Intent.smalltalk:
            # Greetings, thanks and "how are you" are answered by free canned rules (route.shortcut). Anything else the router
            # calls smalltalk ("what do you like to eat?") gets a fixed redirect: a model must never free-write small talk,
            # because it invents a personality ("I love pizza!").
            reply = (route.reply or SMALLTALK_FALLBACK) if route.shortcut else REDIRECT_REPLY
            out = check_output(reply, source_text=reply, secrets=self._secrets(p))  # smalltalk still gets leak/vendor checks
            yield self._done(base, out.text, grounded=False)
            return
        if route.intent is Intent.my_data:
            async for ev in self._my_data(p, message, state.summary, base):
                yield ev
            return

        # platform_faq: retrieval always runs on the standalone (rewritten) query.
        # Scope comes from the JWT principal only; the LLM never chooses whose data to fetch.
        query = route.query
        points_at_page = bool(page_doc and DEICTIC.search(message))
        if points_at_page:
            query = f"{page_doc.title}: {route.query}"  # make "explain this" searchable: add what "this" is
        vec = qvec if (qvec is not None and query == pre_text) else await embed_query(query)
        if use_cache and self.s.cache_followups and checked_vec_for != route.query:  # L2b (opt-in): rewritten follow-ups
            hit = await self.cache.lookup(p, vec, version)
            if hit:
                async for ev in self._serve_cached(p, sid, state, message, hit, "post_router", base, page_doc):
                    yield ev
                return

        # a free follow-up rewrite glues the previous question on: also search the NEW message on its own
        also = [(message, await embed_query(message))] if (route.shortcut and route.query != message and not points_at_page) else None
        res = await self.retriever.retrieve(query, p, k=self.s.knowledge_top_k, vec=vec, also=also,
                                            pin_source=page_doc.source if page_doc else None, pin_first=points_at_page)
        naive = self._naive(p, page_doc, state, message)
        if not res.hits or res.confidence < self.s.min_retrieval_score:
            yield self._done(base, NO_INFO_REPLY, grounded=False, refused_reason="low_confidence",
                             tokens_total=base["router_tokens"], naive_tokens=naive)
            return

        hits = await compress_hits(res.hits, [res.vec, *(v for _, v in (also or []))], self.s) if self.s.enable_compression else res.hits
        mems = await self._recall(p, res.vec)
        messages, used = build_answer_messages(
            summary=state.summary, turns=state.turns, hits=hits, question=message,
            memories=[m.text for m in mems], memory_cap=self.s.memory_prompt_tokens,
            total_budget=self.s.answer_total_budget, context_cap=self.s.context_token_budget,
        )
        raw_parts, final = [], None
        try:
            # Consume the model stream server-side. NOTHING is sent to the client until the output guard has
            # approved the full text, so a leak can never reach the wire (see DECISIONS D31).
            async for kind, val in self.llm.answer_stream(messages):
                if kind == "delta":
                    raw_parts.append(val)
                else:
                    final = val
        except LLMError as exc:
            log.error("answer_failed cause=%r", exc.__cause__)
            yield {"type": "error", "message": "The assistant is busy right now. Please try again."}
            return

        raw = (final.text if final else "".join(raw_parts)).strip()
        if final:
            await self._record(p, final.input_tokens, "answer_in")
            await self._record(p, final.output_tokens, "answer_out")

        # L7: authoritative output check on the full text. Context = what the model was legitimately given.
        source = "\n".join([*(h.text for h in used), message, state.summary,
                            *(f"{t.user} {t.assistant}" for t in state.turns)])
        full_knowledge = "\n".join(h.text for h in res.hits)  # uncompressed: numbers from dropped sentences are still knowledge
        out = check_output(raw, source_text=source, secrets=self._secrets(p), min_support=self.s.grounding_min_support,
                           numbers_source=source + "\n" + full_knowledge)
        for chunk in _chunks(out.text):
            yield {"type": "token", "t": chunk}
        answer = out.text
        blocked = out.action == "replaced"
        sources = [] if blocked else cited_sources(answer, used)
        truncated = bool(final and final.finish_reason == "length")

        if not blocked:  # never persist a blocked answer into context, summary, memory or cache
            await self._persist_turn(p, sid, state, message, answer)
            if (use_cache and not had_history and not mems and not truncated and out.action == "pass"
                    and cacheable(message, answer)):
                await self.cache.store(p, vec, route.query, answer, sources, version)
        if mems:
            self._spawn(self._touch(p, [m.id for m in mems]))
        yield self._done(
            base, answer, grounded=not blocked, refused_reason=out.reason if blocked else None, sources=sources,
            input_tokens=final.input_tokens if final else 0, output_tokens=final.output_tokens if final else 0,
            model=final.model if final else None, truncated=truncated,
            standalone_query=route.query, memories_used=len(mems), naive_tokens=naive,
            tokens_total=base["router_tokens"] + (final.input_tokens + final.output_tokens if final else 0),
        )

    # ------------------------------------------------------------------ helpers
    async def _serve_cached(self, p, sid, state, message, hit, kind, base, page_doc: Doc | None = None) -> AsyncIterator[Event]:
        """A cache hit: 0 LLM tokens. The turn is still recorded so follow-ups keep working."""
        naive = self._naive(p, page_doc, state, message)
        for chunk in _chunks(hit.answer):
            yield {"type": "token", "t": chunk}
        await self._persist_turn(p, sid, state, message, hit.answer)
        yield self._done(base, hit.answer, grounded=True, sources=hit.sources, cache_hit=kind,
                         standalone_query=hit.question, memories_used=0, input_tokens=0, output_tokens=0,
                         tokens_total=0, naive_tokens=naive)

    async def _persist_turn(self, p: Principal, sid: str, state: SessionState, message: str, answer: str) -> None:
        """Save the turn; fold into the rolling summary lazily (only a turn that leaves the 2-turn window)."""
        state.turns.append(Turn(message, answer))
        state.history_tokens += count_tokens(message) + count_tokens(answer)
        evicted: list[Turn] = []
        if self.s.lazy_summary:
            while len(state.turns) > MAX_TURNS:
                evicted.append(state.turns.pop(0))
            await self.sessions.save(p.user_id, sid, state)
            if self.s.summary_mode == "extractive":
                for t in evicted:  # free: remember WHAT was asked; answers can always be re-retrieved
                    state.summary = fold_summary(state.summary, t.user, self.s.summary_max_tokens)
                await self.sessions.save(p.user_id, sid, state)
            else:
                for t in evicted:
                    self._spawn(self._update_summary(p, sid, t.user, t.assistant))
        else:  # legacy behaviour: summarise every turn
            await self.sessions.save(p.user_id, sid, state)
            self._spawn(self._update_summary(p, sid, message, answer))
        if self.memory and self.memory.should_extract(message):
            self._spawn(self._remember(p, sid, message))

    def _naive(self, p: Principal, page_doc: Doc | None, state: SessionState, message: str) -> int:
        """ESTIMATE of the input a bot WITHOUT retrieval would send for this turn. To answer anything about the
        platform it must carry every document the user can see, plus the whole chat so far, on every message.
        (Not just the open page: a bot that only saw one page could not answer questions about other lessons,
        blocks or tools.) Input only, so comparing it with our total, which includes output tokens, is conservative."""
        site = self.catalog.total_tokens(p) if self.catalog else 0
        return count_tokens(ANSWER_SYSTEM) + site + state.history_tokens + count_tokens(message) + 30

    @staticmethod
    def _secrets(p: Principal) -> list[str]:
        return [p.user_id, p.org_id, *(x for x in [p.school_id, *p.batch_ids] if x)]

    async def _my_data(self, p: Principal, message: str, summary: str, base: dict) -> AsyncIterator[Event]:
        """Live data via allow-listed read-only tools. The answer is rendered by code from tool output, so the
        model can never invent marks. Nothing from this path is written to session, summary or memory."""
        if self.adapter is None:
            yield self._done(base, MY_DATA_REPLY, grounded=False, refused_reason="my_data_unavailable")
            return
        plan = await plan_tool(self.llm, self.s, message, summary)
        await self._record(p, plan.tokens, "planner")
        if plan.tool is None:
            yield self._done(base, TOOL_HELP, grounded=False, refused_reason="no_tool")
            return
        result = await run_tool(plan.tool, plan.args, Scope.from_principal(p), self.adapter)
        log.info("tool_call tool=%s ok=%s reason=%s", plan.tool, result.ok, result.reason)  # args never logged
        out = check_output(result.text, source_text=result.text, secrets=self._secrets(p), strict_numbers=True)
        yield self._done(base, out.text, grounded=result.ok, refused_reason=None if result.ok else result.reason,
                         tool=plan.tool, router_tokens=base["router_tokens"] + plan.tokens)

    async def _update_summary(self, p: Principal, sid: str, user_msg: str, answer: str) -> None:
        """Incremental: input is the current summary + ONE turn, never the full history."""
        try:
            old = (await self.sessions.get(p.user_id, sid)).summary
            res = await self.llm.chat(
                model=self.s.router_model,
                messages=[{"role": "system", "content": SUMMARY_SYSTEM},
                          {"role": "user", "content": f"OLD SUMMARY: {old or '(none)'}\nUSER: {truncate_tokens(user_msg, 100)}\n"
                                                      f"ASSISTANT: {truncate_tokens(answer, 150)}"}],
                max_tokens=300, temperature=0,
            )
            await self._record(p, res.input_tokens + res.output_tokens, "summary")
            new = truncate_tokens(res.text.strip(), self.s.summary_max_tokens)
            if not new:
                return
            fresh = await self.sessions.get(p.user_id, sid)  # re-read: a newer turn may have been saved
            fresh.summary = new
            await self.sessions.save(p.user_id, sid, fresh)
        except Exception as exc:  # noqa: BLE001 - background; never break the chat
            log.warning("summary_update_failed %s", type(exc).__name__)

    async def _recall(self, p: Principal, vec: list[float]):
        """Long-term memory via the same query embedding. Fail-safe: memory must never break a chat."""
        if not self.memory:
            return []
        try:
            return await self.memory.retrieve(vec, p)
        except Exception as exc:  # noqa: BLE001
            log.warning("memory_recall_failed %s", type(exc).__name__)
            return []

    async def _remember(self, p: Principal, sid: str, message: str) -> None:
        try:
            await self.memory.extract_and_save(p, message, sid)
        except Exception as exc:  # noqa: BLE001 - background
            log.warning("memory_save_failed %s", type(exc).__name__)

    async def _touch(self, p: Principal, ids: list[str]) -> None:
        try:
            await self.memory.store.touch(p, ids)
        except Exception as exc:  # noqa: BLE001
            log.warning("memory_touch_failed %s", type(exc).__name__)
