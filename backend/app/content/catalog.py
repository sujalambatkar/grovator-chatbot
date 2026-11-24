"""The course/help content shown on the page AND used as the chatbot's knowledge: one source of truth.

Visibility is the same rule retrieval uses (role_visibility + org + school), so the page can never show, and the
bot can never quote, a document the user is not allowed to see.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import frontmatter

from app.core.auth import Principal
from app.rag.hardware import load_hw_docs
from app.rag.tokens import count_tokens

CATEGORY_ORDER = ["applications", "gateway", "interfaces", "devices", "concepts", "courses", "lessons", "blocks", "tools", "help"]
CATEGORY_LABEL = {"applications": "Applications", "gateway": "Gateway", "interfaces": "Gateway interfaces", "devices": "Sensors and modules", "concepts": "Concepts",
                  "courses": "Courses", "lessons": "Lessons", "blocks": "Block reference", "tools": "Platform tools", "help": "Help"}
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
    def __init__(self, root: Path, app_root: Path | None = None) -> None:
        self.docs: list[Doc] = []
        self.app_starters: dict[str, list[str]] = {}
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
        for h in load_hw_docs(root):
            self.docs.append(Doc(source=h.source, title=h.title, category=h.category, order=h.order, body=h.body,
                                 role_visibility=h.role_visibility, tokens=count_tokens(h.body)))
        if app_root is not None:  # applications of backend/knowledge/: one page each (a readable summary of the application file)
            from knowledge.loader import validate

            kb = validate(app_root)
            for i, a in enumerate(kb.applications.values()):
                body = _application_page(kb, a)
                self.docs.append(Doc(source=f"applications/{a.id}", title=a.title, category="applications", order=i, body=body,
                                     role_visibility=list(ALL_ROLES), tokens=count_tokens(body)))
                self.app_starters[f"applications/{a.id}"] = list(a.starter_questions)
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


def _application_page(kb, a) -> str:
    """A short, readable page for the demo: what the application is, its steps, wiring and limits (all taken from the files)."""
    rows = "\n".join(f"- {c.sensor_pin} to {c.kit_pin}" for c in a.circuit.connections)
    return (f"{a.summary}\n\n## The 5 step process\n" + "\n".join(f"{s.n}. **{s.title}**: {s.text}" for s in a.steps)
            + f"\n\n## Wiring ({kb.kits[a.circuit.kit].name})\n{rows}\n\n## Limits used in the program\n"
            + "\n".join(f"- {t.name}: {t.rule}" for t in a.thresholds) + f"\n\n{a.safety_note}")
