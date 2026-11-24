from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" turns on startup safety checks (see production_problems)
    environment: str = "development"

    # LLM provider: "groq" (default) or "gemini". With "gemini" the answer/router models below are used instead of
    # ANSWER_MODEL/ROUTER_MODEL. Model names are env-driven because providers deprecate models.
    llm_provider: str = "groq"
    gemini_api_key: str = ""
    gemini_answer_model: str = "gemini-3.5-flash-lite"
    gemini_router_model: str = "gemini-3.5-flash-lite"
    gemini_thinking_budget: int = -1   # -1 = leave the model default; 0 = no hidden thinking tokens (flash-lite models accept 0)

    # LLM (Groq).
    groq_api_key: str = ""
    router_model: str = "openai/gpt-oss-20b"
    answer_model: str = "openai/gpt-oss-120b"
    guard_model: str = "openai/gpt-oss-safeguard-20b"
    # gpt-oss are reasoning models: hidden reasoning counts as output tokens, so keep it low
    reasoning_effort: str = "low"
    llm_timeout_s: float = 20.0
    llm_max_retries: int = 3

    # Sessions (short-term memory). "memory" = in-process fake Redis (tests/quick demo)
    redis_url: str = "memory"
    session_ttl_s: int = 86400
    summary_max_tokens: int = 120
    summary_mode: str = "extractive"  # "extractive" = free list of earlier questions; "llm" = small-model summary each eviction
    answer_total_budget: int = 1200

    # Long-term memory (stored in the RAG pipeline: `user_memory` collection)
    memory_collection: str = "user_memory"
    memory_top_k: int = 2
    memory_min_score: float = 0.6
    memory_dedupe_score: float = 0.9
    memory_max_per_user: int = 50
    memory_prompt_tokens: int = 80

    # Abuse controls (Redis-backed)
    rate_limit_user_per_min: int = 20
    rate_limit_ip_per_min: int = 60
    trust_proxy: bool = False          # honour X-Forwarded-For only when behind your own proxy
    daily_token_budget_user: int = 50_000
    daily_token_budget_org: int = 1_000_000
    grounding_min_support: float = 0.35  # lexical; real good answers >= 0.44, plausible hallucinations <= 0.20
    platform_adapter: str = "mock"     # "mock" = bundled fake data (dev only); "none" = tools disabled

    # Phase 5 token optimisations (each can be switched off; the benchmark compares on/off)
    enable_semantic_cache: bool = True
    cache_threshold: float = 0.95
    cache_ttl_s: int = 7 * 86400
    cache_max_per_scope: int = 300
    cache_followups: bool = False      # off: a cached answer to a REWRITTEN query ignores what the user actually said
    enable_router_shortcut: bool = True
    enable_canned: bool = True         # pre-written answers (0 tokens) for plain what-is / how-to-connect / which-pins questions
    lazy_summary: bool = True          # fold a turn into the summary only when it leaves the 2-turn window
    enable_compression: bool = True
    compress_keep_ratio: float = 0.8   # keep sentences scoring >= ratio * best sentence in the chunk
    compress_max_sents: int = 3         # core (best-matching) sentences per chunk
    compress_window: int = 1            # plus this many neighbours on each side
    compress_chunk_min: float = 0.6    # drop chunks whose best sentence scores below this (rank 1 always kept)
    usage_log_dir: str = "logs"        # JSONL per-request log; "" disables

    # Browser access (Next.js widget calls this API directly). Comma-separated origins; never "*" with auth.
    cors_origins: str = "http://localhost:3000"

    # Auth (assumed JWT shape, see docs/chatbot/DISCOVERY.md)
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"

    # Vector store: "memory" = embedded (tests), otherwise a URL like http://localhost:6333
    qdrant_url: str = "memory"
    knowledge_collection: str = "knowledge"
    embedding_model: str = "BAAI/bge-small-en-v1.5"

    # Retrieval / token budgets
    knowledge_top_k: int = 4
    min_retrieval_score: float = 0.55
    context_token_budget: int = 550
    answer_max_tokens: int = 350
    max_message_chars: int = 1000

    @model_validator(mode="after")
    def _provider_models(self):
        if self.llm_provider not in ("groq", "gemini"):
            raise ValueError("LLM_PROVIDER must be 'groq' or 'gemini'")
        if self.llm_provider == "gemini":  # every call site reads answer_model / router_model: point them at the Gemini names
            self.answer_model, self.router_model = self.gemini_answer_model, self.gemini_router_model
        return self

    @property
    def llm_api_key(self) -> str:
        return self.gemini_api_key if self.llm_provider == "gemini" else self.groq_api_key

    knowledge_dir: str = str(Path(__file__).resolve().parent.parent / "knowledge_src")
    # File-based application knowledge (applications/sensors/blocks/kits/media YAML). "" switches the application assistant off.
    app_knowledge_dir: str = str(Path(__file__).resolve().parent.parent / "knowledge")
    demo_mode: bool = False            # POST /demo/session auto-login for the demo page. Never in production.


DEFAULT_JWT_SECRET = "dev-secret-change-me"


def production_problems(s: Settings) -> list[str]:
    """Things that must not be true in production. The app refuses to start if any are."""
    if s.environment.lower() != "production":
        return []
    problems = []
    if s.jwt_secret == DEFAULT_JWT_SECRET or len(s.jwt_secret) < 32:
        problems.append("JWT_SECRET must be a unique secret of at least 32 characters (it is the default or too short)")
    if not s.llm_api_key:
        problems.append("GEMINI_API_KEY is not set" if s.llm_provider == "gemini" else "GROQ_API_KEY is not set")
    if s.platform_adapter == "mock":
        problems.append("PLATFORM_ADAPTER=mock serves FAKE student data; use a real adapter or PLATFORM_ADAPTER=none")
    if s.redis_url == "memory":
        problems.append("REDIS_URL=memory is per-process and loses sessions, rate limits and budgets; use a real Redis")
    if s.qdrant_url == "memory":
        problems.append("QDRANT_URL=memory loses all memories on restart; use a real Qdrant")
    if s.demo_mode:
        problems.append("DEMO_MODE=true issues logins without credentials; it must be false in production")
    if any("localhost" in o or o.strip() == "*" for o in s.cors_origins.split(",")):
        problems.append("CORS_ORIGINS contains localhost or *; list your real frontend origin(s)")
    return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
