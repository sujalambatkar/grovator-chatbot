"""Rule-based PII / sensitive-content detection (0 LLM tokens). Phase 4 reuses this for input/output guardrails."""
import re

_HARD: dict[str, re.Pattern] = {
    "email": re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "aadhaar": re.compile(r"(?<!\d)\d{4}[\s-]?\d{4}[\s-]?\d{4}(?!\d)"),
    "phone": re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)|\+\d{1,3}[\s-]?\d[\d\s-]{7,}\d"),
    "jwt": re.compile(r"eyJ[\w-]{5,}\.[\w-]{5,}\.[\w-]{5,}"),
    "api_key": re.compile(r"\b(?:gsk|sk|pk|ghp|xox[a-z])[-_][A-Za-z0-9_-]{16,}"),
    # "pin" is also a hardware word ("the SDA pin is GPIO21"): as a credential it is only a PIN/OTP when a code (3-8 digits) follows.
    "credential": re.compile(r"\b(?:(?:password|passcode|passwd|secret|token)\b\s*(?:is|was|=|:)\s*\S+|(?:pin|otp)\b\s*(?:is|was|=|:)\s*\d{3,8}\b)", re.I),
}

# Not machine identifiers, but must never be written to long-term memory (esp. for school students).
_SENSITIVE: dict[str, re.Pattern] = {
    "health": re.compile(
        r"\b(?:diagnos\w*|asthma|diabet\w*|epilep\w*|adhd|autis\w*|allerg\w*|medicine|medication|"
        r"disabilit\w*|anxiety|depress\w*|surgery|therap\w*|illness|sick)\b", re.I),
    # First- AND third-person: the extractor rewrites memories in third person ("Student's mother ...").
    "personal": re.compile(
        r"\b(?:my name is|(?:name|called) is|i live (?:in|at)|lives (?:in|at)|(?:home )?address|"
        r"mom|mother|dad|father|parents?|brother|sister|family|birthday|age is|\d{1,2} years old|"
        r"phone number|lives with)\b", re.I),
}


def detect_pii(text: str) -> list[str]:
    return [name for name, rx in _HARD.items() if rx.search(text)]


def detect_sensitive(text: str) -> list[str]:
    return detect_pii(text) + [name for name, rx in _SENSITIVE.items() if rx.search(text)]


def redact(text: str) -> str:
    for name, rx in _HARD.items():
        text = rx.sub(f"[{name.upper()}]", text)
    return text
