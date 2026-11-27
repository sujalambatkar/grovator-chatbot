"""Make model output readable in the chat widget: no HTML, no markdown tables.

The widget renders a small, safe markdown subset (headings, lists, **bold**, `code`). Models still like to answer with tables and
`<br>` tags, which show up as rows of pipes and angle brackets. `tidy()` rewrites them as headings and bullets before the answer is
streamed, so a student never sees raw markup.
"""
import re

_BR = re.compile(r"<\s*br\s*/?\s*>|</\s*(?:p|div|li|tr)\s*>", re.I)
_TAG = re.compile(r"</?\s*[a-zA-Z][^<>\n]{0,80}>")
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_STEP = re.compile(r"^\s*(?:\d{1,2}\uFE0F?\u20E3|\d{1,2}[.)])\s*")
_ROW = re.compile(r"^\s*\|.*\|\s*$")


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _table_to_list(header: list[str], rows: list[list[str]]) -> list[str]:
    """One bold title per row (its first cell), then one bullet per remaining column. A cell with several `<br>` parts becomes several bullets."""
    out: list[str] = []
    for r in rows:
        r = r + [""] * (len(header) - len(r))
        title = re.sub(r"\s*\n\s*", " ", r[0]).strip() or "-"
        out.append(f"**{title.strip('*')}**")
        for h, cell in zip(header[1:], r[1:]):
            parts = [p.strip() for p in cell.split("\n") if p.strip()]
            if not parts:
                continue
            label = h.strip().strip("*")
            if len(parts) == 1:
                out.append(f"- **{label}:** {parts[0]}" if label else f"- {parts[0]}")
            else:  # several lines in one cell are steps: label, then a numbered list
                out.append(f"- **{label}:**" if label else "-")
                out += [f"{n}. {_STEP.sub('', p)}" for n, p in enumerate(parts, 1)]
        out.append("")
    return out


def plain_math(text: str) -> str:
    """The widget shows plain text: \\( R_{LDR} \\), \\frac{a}{b}, \\times ... become readable formulas."""
    text = re.sub(r"\\\[(.+?)\\\]", lambda m: "\n" + m.group(1).strip() + "\n", text, flags=re.S)
    text = re.sub(r"\\\((.+?)\\\)", lambda m: m.group(1).strip(), text, flags=re.S)
    text = re.sub(r"_\{([^{}]*)\}", r"_\1", text)   # subscripts first, so \frac arguments have no braces left
    text = re.sub(r"\^\{([^{}]*)\}", r"^\1", text)
    for _ in range(3):
        text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1) / (\2)", text)
    for a, b in ((r"\times", "\u00d7"), (r"\cdot", "\u00b7"), (r"\approx", "\u2248"), (r"\div", "\u00f7"), (r"\pm", "\u00b1"), (r"\le", "\u2264"), (r"\ge", "\u2265"), (r"\;", " "), (r"\,", " "), (r"\left", ""), (r"\right", ""), (r"\text", ""), (r"\mathrm", "")):
        text = text.replace(a, b)
    text = re.sub(r"_\{([^{}]*)\}", r"_\1", text)
    text = re.sub(r"\^\{([^{}]*)\}", r"^\1", text)
    return re.sub(r"\{([^{}]*)\}", r"\1", text) if "\\" not in text and "$" not in text else text


def tidy(text: str) -> str:
    # <br> inside table cells must survive until the cell is split; everywhere else it is a line break
    text = text.replace("\r\n", "\n")
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        if _ROW.match(lines[i]) and i + 1 < len(lines) and _SEP.match(lines[i + 1]):
            header = [_BR.sub("\n", c) for c in _cells(lines[i])]
            i += 2
            rows = []
            while i < len(lines) and _ROW.match(lines[i]):
                rows.append([_BR.sub("\n", c) for c in _cells(lines[i])])
                i += 1
            out += _table_to_list([re.sub(r"\s+", " ", h).strip() for h in header], rows)
            continue
        out.append(lines[i])
        i += 1
    text = plain_math("\n".join(out))
    text = re.sub(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])", r"\1", text)   # *italic* is not rendered by the widget: show the plain word
    text = _BR.sub("\n", text)
    text = _TAG.sub("", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_YN_QUESTION = re.compile(r"^\W*(?:please |so |and |but |ok |okay )*(?:do|does|did|is|are|can|could|should|will|would|must|has|have|am)\b", re.I)
_STRAY = re.compile(r"^\W*(?:yes|no)[.,:!]\s+", re.I)


def drop_stray_verdict(question: str, text: str) -> str:
    """A "what is pressure?" answer must not start with "Yes.": the verdict is only for real yes/no questions."""
    if _YN_QUESTION.search(question) or not _STRAY.match(text):
        return text
    rest = _STRAY.sub("", text, count=1)
    return rest[:1].upper() + rest[1:]


_WIRING_WORDS = re.compile(r"\b(?:GPIO ?-?\d*|VCC|GND|VIN)\b|\b(?i:jumper wires?|breadboard|wir(?:e|es|ed|ing)|connect(?:s|ed|ing|ion|ions)?|plug(?:s|ged|ging)?|pin numbers?)\b")


def mentions_wiring(text: str) -> bool:
    return bool(_WIRING_WORDS.search(text))


def strip_wiring(text: str) -> str:
    """An explanation of how a sensor measures must not drift into wiring: drop the lines / sentences that talk about pins, power or connections."""
    kept: list[str] = []
    for line in text.split("\n"):
        if not _WIRING_WORDS.search(line):
            kept.append(line)
            continue
        if re.match(r"^\s*(?:[-*\u2022]|\d{1,2}[.)])\s", line) or line.lstrip().startswith("#"):
            continue   # a list item or heading about wiring: drop it whole
        sents = [s for s in re.split(r"(?<=[.!?])\s+", line) if not _WIRING_WORDS.search(s)]
        if sents:
            kept.append(" ".join(sents))
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out or text
