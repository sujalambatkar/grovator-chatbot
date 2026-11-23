"""L7 output guardrails: deterministic checks on the final answer (0 LLM tokens).

Order: empty -> vendor/model disclosure -> system-prompt leak -> internal IDs -> unsafe content ->
PII redaction -> numeric grounding -> lexical grounding. Any hard failure replaces the whole answer
with a fixed template; PII/ID redaction only rewrites the offending spans.
"""
import re
from dataclasses import dataclass

from app.guardrails.input import ABUSE, normalise
from app.guardrails.pii import redact
from app.llm import prompts as P

_VENDOR = re.compile(
    r"\b(?:groq|openai|gpt[- ]?oss|gpt-?\d\w*|chatgpt|llama\w*|meta ai|anthropic|claude|qwen|mistral|gemini|"
    r"large language model|llm)\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_OBJECTID = re.compile(r"\b[0-9a-f]{24}\b", re.I)
_NUM = re.compile(r"(?<![\w.])\d+(?:\.\d+)?%?")
_LIST_MARKER = re.compile(r"(?m)^\s*\d{1,2}[.)]\s+")
_WORD = re.compile(r"[a-z]{4,}")
# A number only matters in FAQ answers when it is a claim about performance (marks, scores, ranks...).
_PERF = re.compile(r"scor|mark|grade|percent|rank|attendance|out of|result|passed|failed|\bgot\b|\bpoints?\b|"
                   r"progress|complet|finish|achiev|average|accura|lessons? done", re.I)
_STOP = set("""that this with from your have will them they their there then than were been being into onto over
under about above below also just only very more most some such each other which while where when what whose
whom should could would might must shall does doing done make makes made like need needs want wants using used
uses use can cannot here these those because before after again once both either neither""".split())


@dataclass
class OutputVerdict:
    text: str
    action: str           # "pass" | "redacted" | "replaced"
    reason: str | None = None


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s", "ly"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


def _content_words(text: str) -> list[str]:
    text = re.sub(r"[*_`#>|~-]", " ", text.lower())
    return [_stem(w) for w in _WORD.findall(text) if w not in _STOP]


def support_ratio(answer: str, source: str) -> float:
    """Fraction of the answer's content words that also occur in the allowed source text."""
    words = _content_words(answer)
    if len(words) < 4:
        return 1.0
    src = set(_content_words(source))
    return sum(w in src for w in words) / len(words)


def cited_sources(answer: str, hits: list) -> list[str]:
    """Titles of the retrieved documents the answer actually draws on (word overlap), not every retrieved one."""
    if not hits:
        return []
    aw = set(_content_words(answer))
    if not aw:
        return [hits[0].title]
    scored = [(h.title, len(aw & set(_content_words(h.text))) / len(aw)) for h in hits]
    best = max(sc for _, sc in scored)
    keep = [t for t, sc in scored if sc >= max(0.25, 0.75 * best)]
    return list(dict.fromkeys(keep)) or [max(scored, key=lambda x: x[1])[0]]


def _numbers(text: str) -> set[str]:
    return {n.rstrip("%") for n in _NUM.findall(text)}


def _shingles(text: str, n: int = 6) -> set[tuple[str, ...]]:
    w = re.findall(r"[a-z0-9']+", text.lower())
    return {tuple(w[i : i + n]) for i in range(max(0, len(w) - n + 1))}


def check_output(
    answer: str, *, source_text: str, secrets: list[str] | None = None, strict_numbers: bool = False,
    numbers_source: str | None = None,
    min_support: float = 0.35, prompts: tuple[str, ...] = (P.ANSWER_SYSTEM, P.ROUTER_SYSTEM, P.SUMMARY_SYSTEM, P.EXTRACT_SYSTEM),
) -> OutputVerdict:
    text = answer.strip()
    if not text:
        return OutputVerdict(P.NO_INFO_REPLY, "replaced", "empty")
    if _VENDOR.search(text):
        return OutputVerdict(P.IDENTITY_REPLY, "replaced", "model_disclosure")
    # Phrases that are public (in the retrieved guide or the user's own message) are not secrets even if the instructions use them too.
    leaked = _shingles(text) - _shingles(source_text)
    if any(leaked & _shingles(pr) for pr in prompts):
        return OutputVerdict(P.UNSAFE_REPLY, "replaced", "prompt_leak")
    if any(rx.search(normalise(text)) for rx in ABUSE):
        return OutputVerdict(P.ABUSE_REPLY, "replaced", "unsafe_output")

    redacted = False
    new = _UUID.sub("[ID]", text)
    new = _OBJECTID.sub("[ID]", new)
    for sec in secrets or []:
        if sec and len(sec) >= 2:
            new = re.sub(rf"(?<![\w-]){re.escape(sec)}(?![\w-])", "[ID]", new, flags=re.I)
    new = redact(new)
    redacted = new != text
    text = new

    # Numeric grounding: marks/percentages must come from the source, never from the model's imagination.
    body = _LIST_MARKER.sub("", text)
    # Numbers may come from ANY retrieved knowledge, not only the excerpt the model saw (compression drops sentences).
    src_nums = _numbers(numbers_source if numbers_source is not None else source_text)
    for m in _NUM.finditer(body):
        n = m.group().rstrip("%")
        if n in src_nums:
            continue
        near = body[max(0, m.start() - 35) : m.end() + 35]
        if strict_numbers or _PERF.search(near):
            return OutputVerdict(P.NO_INFO_REPLY, "replaced", "ungrounded_number")

    if support_ratio(text, source_text) < min_support:
        return OutputVerdict(P.NO_INFO_REPLY, "replaced", "ungrounded")
    return OutputVerdict(text, "redacted" if redacted else "pass", "pii_or_id_redacted" if redacted else None)


_PIN = re.compile(r"\bGPIO\s?-?(\d{1,2})\b", re.I)
_NEG = re.compile(r"\b(?:not|n't|no|never|cannot|unsupported|undocumented|isn't|aren't)\b", re.I)


def unsupported_pins(answer: str, knowledge: str, user_message: str = "") -> list[str]:
    """GPIO numbers in the answer that the retrieved gateway records do not contain: a made-up pin is the worst hardware answer.
    A pin the USER named may be mentioned only inside a sentence that negates it ("GPIO7 is not a documented pin")."""
    known = {m.group(1) for m in _PIN.finditer(knowledge)}
    asked = {m.group(1) for m in _PIN.finditer(user_message)}
    bad = []
    for sent in re.split(r"(?<=[.!?])\s+|\n+", answer):
        for m in _PIN.finditer(sent):
            n = m.group(1)
            if n not in known and not (n in asked and _NEG.search(sent)):
                bad.append(f"GPIO{n}")
    return sorted(set(bad))
