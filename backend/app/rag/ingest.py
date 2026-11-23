"""Load Markdown files with front-matter from knowledge_src/ and index them."""
import re
from datetime import datetime, timezone
from pathlib import Path

import frontmatter

from app.rag.chunking import Chunk, chunk_document
from app.rag.embeddings import embed_docs
from app.rag.retriever import HybridRetriever

ALL_ROLES = ["student", "teacher", "admin", "superadmin"]
_ROLE_OK = set(ALL_ROLES)


def load_documents(root: Path) -> list[Chunk]:
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
    return chunks


async def ingest(root: Path, retriever: HybridRetriever, recreate: bool = True) -> int:
    chunks = load_documents(root)
    await retriever.store.ensure(recreate=recreate)
    for i in range(0, len(chunks), 64):
        batch = chunks[i : i + 64]
        await retriever.store.upsert(batch, await embed_docs([c.text for c in batch]))
    retriever.invalidate()
    await retriever.refresh_version()  # new content => semantic-cache entries for the old version are unreachable
    return len(chunks)
