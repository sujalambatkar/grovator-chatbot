"""Builds the answer prompt under a hard input-token budget (summary + last 2 turns + context)."""
from app.llm.prompts import ANSWER_SYSTEM
from app.memory.session import Turn
from app.rag.compress import build_context
from app.rag.store import Hit
from app.rag.tokens import count_tokens, truncate_tokens

OVERHEAD = 30  # chat-format framing + labels
MIN_CONTEXT = 150


def build_answer_messages(
    *, summary: str, turns: list[Turn], hits: list[Hit], question: str,
    total_budget: int, context_cap: int, summary_cap: int = 120, turn_cap: int = 80,
    memories: list[str] | None = None, memory_cap: int = 80, system_hint: str = "") -> tuple[list[dict[str, str]], list[Hit]]:
    summary = truncate_tokens(summary, summary_cap)
    turns = [Turn(truncate_tokens(t.user, turn_cap), truncate_tokens(t.assistant, turn_cap)) for t in turns]
    question = truncate_tokens(question, 300)
    mem_block = ""
    if memories:
        lines, used_tok = [], 0
        for m in memories:
            t = count_tokens(m) + 2
            if used_tok + t > memory_cap:
                break
            lines.append(f"- {m}")
            used_tok += t
        mem_block = "\n".join(lines)

    def fixed() -> int:
        return (count_tokens(ANSWER_SYSTEM) + count_tokens(summary) + count_tokens(mem_block) + count_tokens(question) + OVERHEAD
                + sum(count_tokens(t.user) + count_tokens(t.assistant) for t in turns))

    while mem_block and total_budget - fixed() < MIN_CONTEXT:  # memory is the least important block
        mem_block = ""
    while turns and total_budget - fixed() < MIN_CONTEXT:  # drop oldest turn first
        turns = turns[1:]
    if total_budget - fixed() < MIN_CONTEXT:
        summary = ""
    context, used = build_context(hits, min(context_cap, max(0, total_budget - fixed())))

    parts = []
    if summary:
        parts.append(f"CONVERSATION SUMMARY: {summary}")
    if mem_block:
        parts.append(f"ABOUT THE USER:\n{mem_block}")
    parts.append(f"CONTEXT:\n{context}")
    parts.append(f"QUESTION: {question}")
    messages = [{"role": "system", "content": ANSWER_SYSTEM + system_hint}]
    for t in turns:
        messages += [{"role": "user", "content": t.user}, {"role": "assistant", "content": t.assistant}]
    messages.append({"role": "user", "content": "\n\n".join(parts)})
    return messages, used
