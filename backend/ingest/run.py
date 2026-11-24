"""`python -m ingest.run`: validate -> chunk -> embed -> index the file-based knowledge (backend/knowledge/).

With a persistent vector store (QDRANT_URL=http://...) the index is rebuilt there. With QDRANT_URL=memory the store lives
inside the API process and is rebuilt on every start, so this command then only validates, chunks and embeds (a dry run
that proves the content can be indexed) and prints the section list.
"""
import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from app.config import get_settings
from app.rag.embeddings import embed_docs
from app.rag.ingest import ingest, load_app_chunks
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.rag.tokens import count_tokens
from knowledge.loader import KnowledgeError


async def main(verbose: bool) -> int:
    s = get_settings()
    try:
        chunks = load_app_chunks()  # validates first
    except KnowledgeError as exc:
        print("Knowledge validation failed:")
        for p in exc.problems:
            print(f"  - {p}")
        return 1
    kinds = Counter(c.extra.get("type", "?") for c in chunks)
    print(f"validated + chunked: {len(chunks)} sections {dict(kinds)}; largest = {max(count_tokens(c.text) for c in chunks)} tokens")
    if verbose:
        for c in chunks:
            print(f"  {count_tokens(c.text):5d} tok  {c.source}")
    vecs = await embed_docs([c.text for c in chunks])
    print(f"embedded: {len(vecs)} vectors of size {len(vecs[0])}")
    if s.qdrant_url == "memory":
        print("QDRANT_URL=memory: the index is rebuilt in the API process at start-up, nothing to persist. (Set QDRANT_URL to keep it.)")
        return 0
    r = HybridRetriever(KnowledgeStore(make_client(s.qdrant_url), s.knowledge_collection))
    n = await ingest(Path(s.knowledge_dir), r, app_root=Path(s.app_knowledge_dir) if s.app_knowledge_dir else None)
    print(f"indexed {n} chunks (all sources) into {s.qdrant_url}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    sys.exit(asyncio.run(main(ap.parse_args().verbose)))
