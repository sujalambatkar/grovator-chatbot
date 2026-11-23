"""Markdown -> 300-400 token chunks with small overlap, heading context and a 1-line summary."""
import re
import uuid
from dataclasses import dataclass, field

from app.rag.tokens import count_tokens

TARGET_TOKENS = 350
MAX_TOKENS = 400
OVERLAP_TOKENS = 40


@dataclass
class Chunk:
    id: str
    text: str
    title: str
    source: str
    summary: str
    role_visibility: list[str]
    org_id: str = "*"
    school_id: str = "*"
    updated_at: str = ""
    extra: dict = field(default_factory=dict)

    def payload(self) -> dict:
        return {
            "text": self.text,
            "title": self.title,
            "source": self.source,
            "summary": self.summary,
            "role_visibility": self.role_visibility,
            "org_id": self.org_id,
            "school_id": self.school_id,
            "updated_at": self.updated_at,
        }


_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if s.strip()]


def _sections(body: str) -> list[tuple[str, str]]:
    """Split on markdown headings -> [(heading, text)]."""
    out: list[tuple[str, str]] = []
    heading, buf = "", []
    for line in body.splitlines():
        if line.strip().startswith("```"):
            continue  # code-fence markers are formatting, not content
        if re.match(r"^#{1,3}\s", line):
            if buf:
                out.append((heading, "\n".join(buf).strip()))
            heading, buf = line.lstrip("# ").strip(), []
        else:
            buf.append(line)
    if buf:
        out.append((heading, "\n".join(buf).strip()))
    return [(h, t) for h, t in out if t]


def chunk_document(
    body: str, *, title: str, source: str, role_visibility: list[str],
    org_id: str = "*", school_id: str = "*", updated_at: str = "",
) -> list[Chunk]:
    chunks: list[Chunk] = []

    def emit(heading: str, sents: list[str]) -> None:
        text = " ".join(sents)
        prefix = f"{title} > {heading}: " if heading else f"{title}: "
        full = prefix + text
        cid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source}#{len(chunks)}"))
        chunks.append(Chunk(
            id=cid, text=full, title=title, source=source, summary=(prefix + sents[0])[:160],
            role_visibility=role_visibility, org_id=org_id, school_id=school_id, updated_at=updated_at,
        ))

    for heading, text in _sections(body):
        cur: list[str] = []
        cur_tok = 0
        for sent in _sentences(text):
            st = count_tokens(sent)
            if cur and cur_tok + st > TARGET_TOKENS:
                emit(heading, cur)
                # overlap: carry trailing sentences up to OVERLAP_TOKENS
                tail: list[str] = []
                t = 0
                for s in reversed(cur):
                    if t + count_tokens(s) > OVERLAP_TOKENS:
                        break
                    tail.insert(0, s)
                    t += count_tokens(s)
                cur, cur_tok = tail, t
            cur.append(sent)
            cur_tok += st
        if cur:
            emit(heading, cur)
    return chunks
