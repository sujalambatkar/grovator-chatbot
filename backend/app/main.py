from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.admin import router as admin_router
from app.api.content import router as content_router
from app.api.demo import router as demo_router
from app.content.catalog import ContentCatalog
from app.api.chat import router as chat_router
from app.api.memories import router as memories_router
from app.chat.cache import SemanticCache
from app.chat.service import ChatService
from app.core.abuse import RateLimiter, TokenBudget
from app.core.usage import UsageTracker
from app.config import get_settings, production_problems
from app.llm.llm_client import make_llm
from app.memory.long_term import MemoryManager, MemoryStore
from app.memory.session import SessionStore, make_redis
from app.rag.ingest import ingest
from app.rag.retriever import HybridRetriever
from app.rag.store import KnowledgeStore, make_client
from app.tools.mock_adapter import MockAdapter


def create_app(llm=None, retriever: HybridRetriever | None = None, redis=None, auto_ingest: bool = True,
               adapter=None, settings=None) -> FastAPI:
    settings = settings or get_settings()
    if problems := production_problems(settings):
        raise RuntimeError("Refusing to start in production:\n - " + "\n - ".join(problems))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        r = retriever
        if r is None:
            r = HybridRetriever(KnowledgeStore(make_client(settings.qdrant_url), settings.knowledge_collection))
            # In-memory store is empty on every boot; a persistent store is filled by scripts/ingest.py
            if auto_ingest and settings.qdrant_url == "memory":
                await ingest(Path(settings.knowledge_dir), r, app_root=Path(settings.app_knowledge_dir) if settings.app_knowledge_dir else None)
            elif settings.qdrant_url != "memory":
                await r.refresh_version()
        catalog = ContentCatalog(Path(settings.knowledge_dir))
        rds = redis or make_redis(settings.redis_url)
        sessions = SessionStore(rds, settings.session_ttl_s)
        app.state.limiter = RateLimiter(rds)
        budget = TokenBudget(rds, settings.daily_token_budget_user, settings.daily_token_budget_org)
        data = adapter if adapter is not None else (MockAdapter() if settings.platform_adapter == "mock" else None)
        llm_client = llm or make_llm(settings)
        mem_store = MemoryStore(r.store.client, settings.memory_collection)
        await mem_store.ensure()
        app.state.service = ChatService(llm_client, r, sessions, settings, MemoryManager(mem_store, llm_client, settings),
                                       budget=budget, adapter=data, cache=SemanticCache(rds, settings),
                                       tracker=UsageTracker(rds, settings.usage_log_dir), catalog=catalog)
        app.state.catalog = catalog
        app.state.budget = budget
        yield
        await app.state.service.drain()

    app = FastAPI(title="Grovator AI Assistant", lifespan=lifespan)
    app.state.settings = settings
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip() and o.strip() != "*"]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                       allow_methods=["GET", "POST", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"],
                       expose_headers=["Retry-After"], max_age=600)
    if settings.app_knowledge_dir and (Path(settings.app_knowledge_dir) / "media").is_dir():
        from fastapi.staticfiles import StaticFiles  # images of the knowledge base (lesson slides, kit photos)

        app.mount("/media", StaticFiles(directory=Path(settings.app_knowledge_dir) / "media"), name="media")
    app.include_router(chat_router)
    app.include_router(memories_router)
    app.include_router(admin_router)
    app.include_router(content_router)
    app.include_router(demo_router)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
