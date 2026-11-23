import asyncio
from functools import lru_cache

import numpy as np
from fastembed import TextEmbedding

from app.config import get_settings


@lru_cache
def _model() -> TextEmbedding:
    return TextEmbedding(get_settings().embedding_model)


def embed_docs_sync(texts: list[str]) -> list[list[float]]:
    return [np.asarray(v).tolist() for v in _model().passage_embed(texts)]


def embed_query_sync(text: str) -> list[float]:
    return np.asarray(next(iter(_model().query_embed([text])))).tolist()


async def embed_docs(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(embed_docs_sync, texts)


async def embed_query(text: str) -> list[float]:
    return await asyncio.to_thread(embed_query_sync, text)
