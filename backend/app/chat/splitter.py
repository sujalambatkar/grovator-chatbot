"""Message shape helpers: greetings in front of a question, and several questions in one message."""
import re

MAX_PARTS = 3
_GREETING_ONLY = re.compile(r"^\W*(?:hi+|hello+|hey+|hola|namaste|good (?:morning|afternoon|evening)|please|pls|thanks?|thank you|ok(?:ay)?|so|and|also)\W*$", re.I)
_GREETING_PREFIX = re.compile(r"^\W*(?:hi+|hello+|hey+|hola|namaste|good (?:morning|afternoon|evening))\b[\s,!.:-]*(?:there[\s,!.:-]*)?", re.I)


def strip_greeting(message: str) -> str:
    """"hello tell me about grovator" -> "tell me about grovator". A message that is ONLY a greeting is left alone (small talk handles it)."""
    rest = _GREETING_PREFIX.sub("", message, count=1).strip()
    return rest if len(re.findall(r"\w+", rest)) >= 2 else message


_QUESTION_START = re.compile(r"^\W*(?:what|how|why|where|which|who|when|is|are|do|does|did|can|could|should|will|would|must|has|have|am)\b", re.I)


def _question_like(piece: str) -> bool:
    return piece.rstrip().endswith("?") or bool(_QUESTION_START.match(piece))


def split_questions(message: str) -> tuple[list[str], int]:
    """(parts, dropped). One part when the message is a single question; otherwise each question on its own, at most MAX_PARTS.
    Split on a question mark followed by more text, and on new lines."""
    pieces = [p.strip() for p in re.split(r"(?<=\?)\s+|\n+", message.strip()) if p.strip()]
    pieces = [p for p in pieces if len(re.findall(r"\w+", p)) >= 2 and not _GREETING_ONLY.match(p)]
    parts: list[str] = []
    for piece in pieces:   # "Explain the formula." after a question is part of that question, not a question of its own
        if parts and not _question_like(piece):
            parts[-1] += " " + piece
        else:
            parts.append(piece)
    if len(parts) <= 1:
        return ([parts[0]] if parts else [message.strip()]), 0
    return parts[:MAX_PARTS], max(0, len(parts) - MAX_PARTS)
