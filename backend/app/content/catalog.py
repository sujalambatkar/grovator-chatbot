"""The course/help content shown on the page AND used as the chatbot's knowledge: one source of truth.

Visibility is the same rule retrieval uses (role_visibility + org + school), so the page can never show, and the
bot can never quote, a document the user is not allowed to see.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from app.core.auth import Principal
from app.rag.tokens import count_tokens

CATEGORY_ORDER = ["courses", "lessons", "blocks", "tools", "help"]
CATEGORY_LABEL = {"courses": "Courses", "lessons": "Lessons", "blocks": "Block reference", "tools": "Platform tools", "help": "Help"}
ALL_ROLES = ["student", "teacher", "admin", "superadmin"]


@dataclass
class Doc:
    source: str
    title: str
    category: str
    order: int
    body: str
    role_visibility: list[str]
    org_id: str = "*"
    school_id: str = "*"
    tokens: int = 0


def _title(path: Path) -> str:
    return re.sub(r"[-_]", " ", path.stem).title()


class ContentCatalog:
    def __init__(self, root: Path) -> None:
        self.docs: list[Doc] = []
        for path in sorted(root.rglob("*.md")):
            if path.name.lower() == "readme.md":
                continue
            post = frontmatter.load(path)
            body = post.content.strip()
            self.docs.append(Doc(
                source=str(path.relative_to(root)), title=post.get("title") or _title(path),
                category=str(post.get("category", "help")), order=int(post.get("order", 100)), body=body,
                role_visibility=list(post.get("role_visibility", ALL_ROLES)),
                org_id=str(post.get("org_id", "*")), school_id=str(post.get("school_id", "*")),
                tokens=count_tokens(body)))
        self._by_source = {d.source: d for d in self.docs}
        self._total_cache: dict[tuple, int] = {}

    @staticmethod
    def _can_see(p: Principal, d: Doc) -> bool:
        return (p.role.value in d.role_visibility and d.org_id in ("*", p.org_id)
                and d.school_id in ("*", p.school_id or "*"))

    def visible(self, p: Principal) -> list[Doc]:
        def key(d: Doc):
            cat = CATEGORY_ORDER.index(d.category) if d.category in CATEGORY_ORDER else len(CATEGORY_ORDER)
            return (cat, d.order, d.title)
        return sorted((d for d in self.docs if self._can_see(p, d)), key=key)

    def get(self, p: Principal, source: str | None) -> Doc | None:
        """None for unknown AND for not-visible documents (the two are indistinguishable to the caller)."""
        d = self._by_source.get(source or "")
        return d if d and self._can_see(p, d) else None

    def total_tokens(self, p: Principal) -> int:
        """Tokens of every document the user can see: what a 'paste the whole site into the prompt' bot would send."""
        k = (p.role.value, p.org_id, p.school_id)
        if k not in self._total_cache:
            self._total_cache[k] = sum(d.tokens for d in self.visible(p))
        return self._total_cache[k]
