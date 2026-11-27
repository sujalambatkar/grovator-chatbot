"""Student safety warnings, decided in code from config/safety.yaml (never by the model).

`safety_warnings(message, answer, sensor_ids, kind)` returns the warnings to show under an answer, most serious first.
Rules are data: add or edit entries in config/safety.yaml, no code change.
"""
import re
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / "config" / "safety.yaml"
MAX_WARNINGS = 2
_ORDER = {"danger": 0, "caution": 1}


@lru_cache(maxsize=1)
def _rules(path: str = str(CONFIG)) -> list[dict]:
    rules = (yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}).get("rules", [])
    for r in rules:
        r["_msg"] = re.compile(r["message_words"], re.I) if r.get("message_words") else None
        r["_ans"] = re.compile(r["answer_words"], re.I) if r.get("answer_words") else None
        r["text"] = " ".join(str(r["text"]).split())
    return rules


# A definition or concept question ("what is alcohol?", "explain the flame sensor") is not a hands-on activity: no warning box.
_DEFINITION = re.compile(r"^\W*(?:what(?:'s| is| are| does)|whats|define|meaning of|tell me about|explain|describe|who|why (?:is|are|do|does)|how does [^?]{1,50} work)\b", re.I)
_ACTION = re.compile(r"\b(?:connect\w*|wir(?:e|ed|ing)|plug\w*|attach\w*|hook\w*|test\w*|power\w*|charg\w*|solder\w*|touch\w*|try|build|make|use|using|install\w*|switch\w*|turn\w*|run|measure\w*|put|add|set ?up|fix)\b", re.I)


def safety_warnings(message: str, answer: str, sensor_ids: list[str], kind: str | None = None, intent: str | None = None) -> list[dict]:
    """[{"id", "level", "title", "text"}], danger first, at most MAX_WARNINGS."""
    if not _ACTION.search(message) and (_DEFINITION.search(message) or intent == "general_tech"):
        return []
    out = []
    for r in _rules():
        if r.get("kinds") and kind not in r["kinds"]:
            continue
        hit = (bool(set(r.get("sensors", [])) & set(sensor_ids))
               or bool(r["_msg"] and r["_msg"].search(message))
               or bool(r["_ans"] and r["_ans"].search(answer)))
        if hit:
            out.append({"id": r["id"], "level": r["level"], "title": r["title"], "text": r["text"]})
    out.sort(key=lambda w: _ORDER.get(w["level"], 9))   # stable: file order inside a level
    return out[:MAX_WARNINGS]
