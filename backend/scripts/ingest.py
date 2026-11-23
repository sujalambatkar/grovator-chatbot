"""Index knowledge_src/ into the configured Qdrant. Run: python -m scripts.ingest"""
import asyncio
from pathlib import Path

from app.config import get_settings
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client


async def main() -> None:
    s = get_settings()
    r = HybridRetriever(KnowledgeStore(make_client(s.qdrant_url), s.knowledge_collection))
    n = await ingest(Path(s.knowledge_dir), r)
    print(f"Indexed {n} chunks into '{s.knowledge_collection}' at {s.qdrant_url}")


if __name__ == "__main__":
    asyncio.run(main())
