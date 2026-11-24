"""Load Markdown files with front-matter from knowledge_src/ and index them."""
import re
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

import uuid

from app.rag.chunking import Chunk, chunk_document
from app.rag.embeddings import embed_docs
from app.rag.hardware import load_hw_docs
from app.rag.retriever import HybridRetriever

ALL_ROLES = ["student", "teacher", "admin", "superadmin"]
_ROLE_OK = set(ALL_ROLES)


def load_documents(root: Path, app_root: Path | None = None) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(root.rglob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        post = frontmatter.load(path)
        roles = post.get("role_visibility", ALL_ROLES)
        if not set(roles) <= _ROLE_OK:
            raise ValueError(f"{path}: unknown role in role_visibility {roles}")
        title = post.get("title") or re.sub(r"[-_]", " ", path.stem).title()
        chunks += chunk_document(
            post.content, title=title, source=str(path.relative_to(root)), role_visibility=roles,
            org_id=post.get("org_id", "*"), school_id=post.get("school_id", "*"),
            updated_at=str(post.get("updated_at", datetime.now(timezone.utc).date())),
        )
    for d in load_hw_docs(root):  # structured gateway dataset (JSON) -> documents, generated from the records
        chunks += chunk_document(d.body, title=d.title, source=d.source, role_visibility=d.role_visibility,
                                 updated_at=str(datetime.now(timezone.utc).date()))
    if app_root is not None:
        chunks += load_app_chunks(app_root)
    return chunks


def load_app_chunks(root: Path | None = None) -> list[Chunk]:
    """File-based knowledge (backend/knowledge/*): validated, then one chunk per section (sections are never split)."""
    from knowledge.loader import validate
    from knowledge.render import render_all

    today = str(datetime.now(timezone.utc).date())
    return [Chunk(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"kb:{sec.source}")), text=f"{sec.title}: {sec.text}", title=sec.title,
                  source=sec.source, summary=f"{sec.title}: {sec.text}"[:160], role_visibility=list(ALL_ROLES), updated_at=today,
                  extra=sec.meta)
            for sec in render_all(validate(root))]


async def ingest(root: Path, retriever: HybridRetriever, recreate: bool = True, app_root: Path | None = None) -> int:
    chunks = load_documents(root, app_root)
    await retriever.store.ensure(recreate=recreate)
    for i in range(0, len(chunks), 64):
        batch = chunks[i : i + 64]
        await retriever.store.upsert(batch, await embed_docs([c.text for c in batch]))
    retriever.invalidate()
    await retriever.refresh_version()  # new content => semantic-cache entries for the old version are unreachable
    return len(chunks)
