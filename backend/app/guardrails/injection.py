"""Minimal prompt-injection patterns (0 LLM tokens). Phase 4 expands this into the full input guardrail."""
import re

_PATTERNS = [
    r"ignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier)",
    r"disregard\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|instructions)",
    r"(?:reveal|show|print|repeat|leak)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions)",
    r"system\s+prompt",
    r"you\s+are\s+now\b",
    r"\bjailbreak\b|\bDAN\b|developer\s+mode",
    r"(?:act|pretend|behave)\s+(?:as|like)\b",
    r"<\s*/?\s*(?:system|assistant)\s*>",
    r"[A-Za-z0-9+/]{60,}={0,2}",  # long base64-looking blob
]
_RX = [re.compile(p, re.I) for p in _PATTERNS]


def looks_like_injection(text: str) -> bool:
    return any(rx.search(text) for rx in _RX)
