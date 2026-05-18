from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── App ──────────────────────────────────────────────────────────────────
    APP_PORT: int = 8001
    ENVIRONMENT: str = "dev"
    LOG_LEVEL: str = "INFO"
    APP_TITLE: str = "ICCP AI Service"
    APP_VERSION: str = "1.0.0"
    # Comma-separated list of allowed CORS origins for browser clients.
    # Example: "https://iccp.wyndev.me,https://staging-iccp.wyndev.me"
    CORS_ALLOW_ORIGINS: str = ""

    # ── Gemini ───────────────────────────────────────────────────────────────
    GEMINI_API_KEY: str
    GEMINI_EMBEDDING_MODEL: str = "models/gemini-embedding-001"
    GEMINI_EMBEDDING_OUTPUT_DIMENSION: int = 768
    GEMINI_CHAT_MODEL: str = "gemini-2.5-flash"
    GEMINI_MAX_TOKENS: int = 4096
    GEMINI_TEMPERATURE: float = 0.2

    # ── Beeknoee model gateway ───────────────────────────────────────────────
    # New canonical env names.
    BEEKNOEE_API_KEY: str = ""
    BEEKNOEE_API_BASE: str = "https://platform.beeknoee.com/api/v1"
    BEEKNOEE_MODEL: str = "openai/gpt-oss-120b"

    # Mode-specific model overrides — leave empty to use BEEKNOEE_MODEL for all.
    # GENERAL_CHAT_MODEL: fast/cheap model for plain assistant (no RAG).
    # RAG_CHAT_MODEL: stronger/slower model for grounded Q&A.
    # TOOL_CHAT_MODEL: model for tool-use synthesis (same as RAG by default).
    GENERAL_CHAT_MODEL: str = ""
    RAG_CHAT_MODEL: str = ""
    TOOL_CHAT_MODEL: str = ""

    # Backward-compatible legacy aliases used by older landing-page config.
    LANDING_PAGE_API_KEY: str = ""
    LANDING_PAGE_API_BASE: str = ""
    LANDING_PAGE_MODEL: str = ""

    # ── Pinecone ─────────────────────────────────────────────────────────────
    PINECONE_API_KEY: str
    PINECONE_INDEX_NAME: str = "iccp-knowledge"
    PINECONE_ENVIRONMENT: str = "us-east-1-aws"

    # ── MongoDB ──────────────────────────────────────────────────────────────
    MONGODB_URL: str = "mongodb://mongo:27017"
    MONGODB_DATABASE: str = "iccp_ai"

    # ── Redis ────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # ── Internal communication ───────────────────────────────────────────────
    BE_CORE_BASE_URL: str = "http://iccp-be-core:3333"
    INTERNAL_API_KEY: str = "change-me-internal-key"
    BE_CORE_INTROSPECT_URL: str = "http://iccp-be-core:3333/api/v1/auth/introspect"

    # ── RAG tuning ───────────────────────────────────────────────────────────
    # Chunk size reduced from 384→256 tokens for better small-question recall.
    # Overlap reduced proportionally (25% of chunk size).
    CHUNK_SIZE: int = 256
    CHUNK_OVERLAP: int = 64
    RETRIEVAL_TOP_K: int = 12          # Increased for better recall before rerank
    RETRIEVAL_SCORE_THRESHOLD: float = 0.30  # Minimum score to accept a chunk
    MAX_HISTORY_MESSAGES: int = 10
    EMBEDDING_CACHE_TTL: int = 86400  # 24 hours in seconds
    TOKEN_CACHE_TTL: int = 120  # 2 minutes — token introspect cache
    INTENT_CACHE_TTL: int = 300  # 5 minutes — intent classification cache

    # ── Quota defaults ───────────────────────────────────────────────────────
    DEFAULT_MONTHLY_MESSAGE_LIMIT: int = 1000
    DEFAULT_MONTHLY_INGESTION_LIMIT: int = 100
    DEFAULT_DAILY_USER_MESSAGE_LIMIT: int = 100
    DEFAULT_DAILY_USER_TOKEN_LIMIT: int = 100000
    DEFAULT_ORG_TOKEN_LIMIT: int = 10000000

    # ── OpenSearch ───────────────────────────────────────────────────────────
    OPENSEARCH_URL: str = "http://opensearch:9200"
    OPENSEARCH_INDEX_PREFIX: str = "iccp_documents"
    ENABLE_OPENSEARCH: bool = True

    # ── Feature flags ────────────────────────────────────────────────────────
    ENABLE_RERANKING: bool = False
    ENABLE_QUERY_EXPANSION: bool = False
    ENABLE_ANALYTICS_AGENT: bool = True
    ENABLE_AI_MODEL_CONFIG_SEED: bool = True

    model_config = SettingsConfigDict(
        env_file=".env.dev",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "prod"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "dev"

    @property
    def cors_allow_origins(self) -> list[str]:
        # In development, keep permissive default for local debugging.
        if self.is_development and not self.CORS_ALLOW_ORIGINS.strip():
            return ["*"]

        return [
            origin.strip()
            for origin in self.CORS_ALLOW_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def beeknoee_api_key(self) -> str:
        return self.BEEKNOEE_API_KEY or self.LANDING_PAGE_API_KEY

    @property
    def beeknoee_api_base(self) -> str:
        return self.BEEKNOEE_API_BASE or self.LANDING_PAGE_API_BASE or "https://platform.beeknoee.com/api/v1"

    @property
    def beeknoee_model(self) -> str:
        return self.BEEKNOEE_MODEL or self.LANDING_PAGE_MODEL or "openai/gpt-oss-120b"

    def model_for_mode(self, mode: str) -> str:
        """Return the preferred model name for a given chat mode."""
        if mode == "general" and self.GENERAL_CHAT_MODEL:
            return self.GENERAL_CHAT_MODEL
        if mode in {"rag", "auto"} and self.RAG_CHAT_MODEL:
            return self.RAG_CHAT_MODEL
        if mode == "tool" and self.TOOL_CHAT_MODEL:
            return self.TOOL_CHAT_MODEL
        return self.beeknoee_model


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
