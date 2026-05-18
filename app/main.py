import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncGenerator

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_response(status_code: int, error: str, message: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "statusCode": status_code,
            "timestamp": _now(),
            "error": error,
            "message": message,
            **extra,
        },
    )

from app.api.v1.router import v1_router
from app.core.config import settings
from app.core.exceptions import (
    ICCPAIBaseException,
    TenantIsolationException,
    UnauthorizedException,
    VectorStoreException,
    BeCoreClientException,
    ContentPolicyViolationException,
    PromptInjectionException,
    QuotaExceededException,
)
from app.core.logging import setup_logging

log = structlog.get_logger(__name__)

# ── OpenAPI tag metadata ────────────────────────────────────────────────────
_OPENAPI_TAGS: list[dict[str, Any]] = [
    {
        "name": "Health",
        "description": "Service health check endpoint.",
    },
    {
        "name": "Conversations",
        "description": (
            "Conversation management endpoints. "
            "Create, list, update, delete, archive, and restore conversations.\n\n"
            "Authentication via **Bearer JWT** validated through `iccp_be_core`."
        ),
    },
    {
        "name": "Messages",
        "description": (
            "Message endpoints. Send messages and retrieve history.\n\n"
            "### Streaming\n"
            "The `POST /conversations/{id}/messages` endpoint returns **Server-Sent Events (SSE)**.\n\n"
            "**Event types:**\n"
            "- `token` — partial response text\n"
            "- `done` — final event with `message_id`, `citations`, `web_sources`\n"
            "- `error` — error message\n\n"
            "### Modes\n"
            "- `auto` — internal retrieval + web search when helpful\n"
            "- `rag` — document retrieval via Pinecone\n"
            "- `web` — web search via DuckDuckGo"
        ),
    },
    {
        "name": "Ingestion",
        "description": (
            "Document ingestion pipeline endpoints. "
            "Called **internally by `iccp_be_core`** only.\n\n"
            "Authentication via **`X-Internal-Key`** header.\n\n"
            "### Pipeline\n"
            "`Upload (be_core)` → `POST /ingest/documents` → `Celery task` "
            "→ `Parse → Chunk → Embed → Pinecone upsert`"
        ),
    },
    {
        "name": "Quotas",
        "description": "Quota management for users and organizations.",
    },
    {
        "name": "AI Model Configs",
        "description": (
            "System-admin-only AI model and API key configuration. "
            "Configurations are stored in MongoDB and resolved by billing plan."
        ),
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    setup_logging()
    log.info("iccp_be_ai.starting", environment=settings.ENVIRONMENT)

    # Initialize MongoDB
    from app.db.mongodb import init_mongodb
    from app.db.seeds.ai_model_config_seed import seed_ai_model_configs_if_missing
    await init_mongodb()
    await seed_ai_model_configs_if_missing()
    log.info("mongodb.initialized")

    # Initialize Pinecone
    from app.services.pinecone_service import PineconeService
    await PineconeService.initialize()
    log.info("pinecone.initialized")

    # Initialize OpenSearch connection pool
    if settings.ENABLE_OPENSEARCH:
        from app.services.opensearch_service import OpenSearchService
        OpenSearchService.init_client()
        log.info("opensearch.initialized", url=settings.OPENSEARCH_URL)

    # Initialize httpx client for be_core communication
    from app.clients.be_core_client import BeCoreClient
    await BeCoreClient.initialize()
    log.info("be_core_client.initialized")

    # Initialize Redis client
    import redis.asyncio as aioredis
    app.state.redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    log.info("redis.initialized")

    log.info("iccp_be_ai.ready", port=settings.APP_PORT)
    yield

    # Shutdown
    log.info("iccp_be_ai.shutting_down")
    await BeCoreClient.close()
    await app.state.redis.aclose()

    from app.db.mongodb import close_mongodb
    await close_mongodb()
    log.info("iccp_be_ai.stopped")


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_TITLE,
        version=settings.APP_VERSION,
        description=(
            "## ICCP AI Service\n\n"
            "RAG Chatbot Multi-Agent Service for the **ICCP (Internal Consulting Chatbot Platform)**.\n\n"
            "### Authentication\n"
            "| Endpoint group | Method | Header |\n"
            "|---|---|---|\n"
            "| Conversations / Messages | Bearer JWT | `Authorization: Bearer <token>` |\n"
            "| Ingestion | Internal API Key | `X-Internal-Key: <key>` |\n\n"
            "### Chat Modes\n"
            "| Mode | Description |\n"
            "|---|---|\n"
            "| `auto` | Internal docs + web search when needed |\n"
            "| `rag` | Document retrieval from Pinecone vector store |\n"
            "| `web` | Web search via DuckDuckGo |\n"
            "\n"
            "### Architecture\n"
            "```\n"
            "FE → [Authorization: Bearer JWT] → AI Service\n"
            "AI Service → introspect(jwt) → be_core → user info\n"
            "AI Service → Conversation CRUD → MongoDB\n"
            "AI Service → RAG → Pinecone\n"
            "AI Service → Web search → DuckDuckGo\n"
            "be_core → ingest → AI Service → Celery → Pinecone\n"
            "```"
        ),
        openapi_tags=_OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        swagger_ui_parameters={
            "persistAuthorization": True,
            "displayRequestDuration": True,
            "filter": True,
            "deepLinking": True,
            "defaultModelsExpandDepth": 2,
            "defaultModelExpandDepth": 2,
            "docExpansion": "list",
            "syntaxHighlight.theme": "monokai",
        },
        lifespan=lifespan,
    )

    # ── Middleware ──────────────────────────────────────────────────────────
    # NOTE: GZipMiddleware is intentionally NOT added here.
    # Starlette's GZipMiddleware buffers the entire response body until it
    # reaches `minimum_size` before flushing — this breaks SSE streaming
    # because each token event (~50 bytes) never reaches the 1000-byte threshold
    # and gets held back until the stream ends.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id)
        response = await call_next(request)
        response.headers["X-Trace-ID"] = trace_id
        return response

    # ── Exception handlers ─────────────────────────────────────────────────

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _error_response(exc.status_code, "http_error", detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        details = [
            {
                "property": " -> ".join(str(loc) for loc in err["loc"]),
                "message": err["msg"],
                "code": err["type"],
            }
            for err in exc.errors()
        ]
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "validation_error",
            "Request validation failed",
            details=details,
        )

    @app.exception_handler(QuotaExceededException)
    async def quota_exceeded_handler(request: Request, exc: QuotaExceededException):
        log.warning("quota.exceeded", quota_type=exc.quota_type, detail=exc.message)
        return _error_response(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "quota_exceeded",
            exc.message,
            quota_type=exc.quota_type,
        )

    @app.exception_handler(PromptInjectionException)
    async def injection_exception_handler(request: Request, exc: PromptInjectionException):
        log.warning("security.prompt_injection_blocked", source=exc.source, detail=exc.message)
        return _error_response(status.HTTP_400_BAD_REQUEST, "prompt_injection_detected", exc.message)

    @app.exception_handler(ContentPolicyViolationException)
    async def policy_violation_handler(request: Request, exc: ContentPolicyViolationException):
        log.warning("security.content_policy_violation", violation_type=exc.violation_type)
        return _error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "content_policy_violation",
            exc.message,
            violation_type=exc.violation_type,
        )

    @app.exception_handler(TenantIsolationException)
    async def tenant_exception_handler(request: Request, exc: TenantIsolationException):
        log.warning("tenant.isolation_violation", detail=exc.message)
        return _error_response(status.HTTP_403_FORBIDDEN, "tenant_isolation_error", exc.message)

    @app.exception_handler(UnauthorizedException)
    async def unauthorized_exception_handler(request: Request, exc: UnauthorizedException):
        return _error_response(status.HTTP_401_UNAUTHORIZED, "unauthorized", exc.message)

    @app.exception_handler(VectorStoreException)
    async def vector_store_exception_handler(request: Request, exc: VectorStoreException):
        log.error("vector_store.error", detail=exc.message)
        return _error_response(status.HTTP_503_SERVICE_UNAVAILABLE, "vector_store_error", exc.message)

    @app.exception_handler(BeCoreClientException)
    async def be_core_exception_handler(request: Request, exc: BeCoreClientException):
        log.error("be_core_client.error", status_code=exc.status_code, detail=exc.message)
        return _error_response(status.HTTP_502_BAD_GATEWAY, "upstream_error", exc.message)

    @app.exception_handler(ICCPAIBaseException)
    async def base_exception_handler(request: Request, exc: ICCPAIBaseException):
        log.error("internal_error", detail=exc.message)
        return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "internal_error", exc.message)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        log.error("unhandled_exception", error=str(exc), exc_info=True)
        return _error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_server_error",
            "An unexpected error occurred",
        )

    # ── Routers ────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    # ── Custom OpenAPI schema ──────────────────────────────────────────────
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            tags=_OPENAPI_TAGS,
            routes=app.routes,
        )

        schema.setdefault("components", {})
        schema["components"]["securitySchemes"] = {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "JWT token issued by **iccp_be_core** after login.\n\n"
                    "Get your token from `POST /api/v1/auth/login` on `iccp_be_core`."
                ),
            },
            "InternalApiKey": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Internal-Key",
                "description": "Shared secret between `iccp_be_core` and `iccp_be_ai`.",
            },
            "OrganizationIdHeader": {
                "type": "apiKey",
                "in": "header",
                "name": "x-organization-id",
                "description": (
                    "Organization context header for tenant-scoped endpoints. "
                    "Required by user endpoints that read organization data."
                ),
            },
        }

        for path, path_item in schema.get("paths", {}).items():
            for method, operation in path_item.items():
                if not isinstance(operation, dict):
                    continue
                tags = operation.get("tags", [])
                if (
                    "Conversations" in tags
                    or "Messages" in tags
                    or "Quotas" in tags
                    or "AI Models" in tags
                    or "Landing Pages" in tags
                ):
                    operation["security"] = [{"BearerAuth": [], "OrganizationIdHeader": []}]
                elif "AI Model Configs" in tags:
                    operation["security"] = [{"BearerAuth": []}]
                elif "Ingestion" in tags:
                    operation["security"] = [{"InternalApiKey": []}]

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


app = create_app()
