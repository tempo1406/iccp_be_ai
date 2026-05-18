# ICCP AI Service — Development Rules & Conventions

## 1. General Principles

1. **Single Responsibility:** Each module, class, and function has one clear job.
2. **Explicit over implicit:** No magic. Every behavior must be traceable.
3. **Async first:** All I/O operations (DB, HTTP, Pinecone, Redis) must be `async/await`. Never block the event loop.
4. **Fail fast:** Validate inputs at the API boundary. Raise typed exceptions, never silently swallow errors.
5. **Tenant isolation is non-negotiable:** Every Pinecone query and DB query MUST include `organization_id`. If it's missing, raise 403.
6. **No direct DB writes for be_core tables:** `iccp_be_ai` communicates with be_core's tables only through the internal HTTP API, never via direct SQL writes.

---

## 2. Project & File Conventions

### 2.1 Language
- Python **3.11+**
- All source files: `snake_case.py`
- All class names: `PascalCase`
- All variables and functions: `snake_case`
- Constants: `UPPER_SNAKE_CASE`

### 2.2 File Naming
```
app/api/v1/ingest.py        ✅
app/api/v1/IngestRouter.py  ❌

app/agents/ingestion_agent.py   ✅
app/agents/IngestionAgent.py    ❌
```

### 2.3 Module Structure
Every package must have an `__init__.py`. Internal imports must use absolute paths:
```python
# ✅ Correct
from app.core.config import settings
from app.services.pinecone_service import PineconeService

# ❌ Wrong
from ..config import settings
from services.pinecone_service import PineconeService
```

---

## 3. FastAPI Rules

### 3.1 Router Organization
- Each feature domain has its own router file: `app/api/v1/{domain}.py`
- All routers are registered in `app/api/v1/router.py`
- All routers use prefix `/api/v1/{domain}`
- Tags must match domain name

```python
# app/api/v1/ingest.py
router = APIRouter(prefix="/ingest", tags=["Ingestion"])
```

### 3.2 Request/Response Models
- All request bodies: Pydantic `BaseModel` in `app/schemas/{domain}.py`
- All responses: typed Pydantic models — **never** return raw dicts
- Use `response_model=` on every endpoint

```python
@router.post("/documents", response_model=IngestJobResponse, status_code=202)
async def ingest_document(body: IngestDocumentRequest, ...):
    ...
```

### 3.3 Dependency Injection
- All services injected via `Depends()` — never instantiated inside route handlers
- Use `Annotated` type hints for cleaner DI:

```python
CurrentUser = Annotated[TokenPayload, Depends(get_current_user)]

@router.post("/messages")
async def send_message(body: SendMessageRequest, user: CurrentUser):
    ...
```

### 3.4 Auth & Tenant Guard
- Every non-public endpoint must validate JWT and extract `organization_id`
- JWT is forwarded from `iccp_be_core` — we verify with the same secret
- Inject via `Depends(get_current_user)` which returns `TokenPayload`
- All agents and services receive `organization_id` explicitly — never read from global state

### 3.5 Error Handling
- Use `HTTPException` for expected errors
- Define custom exception classes in `app/core/exceptions.py`
- Register a global exception handler in `main.py`

```python
class VectorStoreException(Exception):
    pass

class DocumentNotFoundException(Exception):
    pass
```

---

## 4. Agent Rules

### 4.1 Agent Interface
Every agent must extend `BaseAgent` and implement the `run()` method:

```python
class BaseAgent(ABC):
    @abstractmethod
    async def run(self, input: AgentInput) -> AgentOutput:
        ...
```

### 4.2 Agent Input/Output
- Agents communicate using typed Pydantic models, never raw dicts
- `AgentInput` must always carry: `organization_id`, `user_id`, `trace_id`
- Agents must NOT perform DB writes directly — they return outputs, the orchestrator writes

### 4.3 Orchestrator Pattern
The `AgentOrchestrator` is the only entry point for chat requests:
```
API route → AgentOrchestrator → RouterAgent → (IngestionAgent | RetrievalAgent | ChatAgent)
```
Never call specialized agents directly from route handlers.

### 4.4 LangGraph State
- LangGraph state must be a typed `TypedDict`
- State must include `messages`, `context_chunks`, `organization_id`, `citations`
- State transitions must be deterministic and logged

### 4.5 Prompt Rules
- All prompts stored in `app/prompts/{name}.py` as constants
- System prompts in Vietnamese for user-facing content
- Never hardcode prompts inside agent classes
- Use `ChatPromptTemplate.from_messages()` — never f-string raw prompts

```python
# app/prompts/rag_chat.py
SYSTEM_PROMPT = """Bạn là trợ lý tư vấn nội bộ của doanh nghiệp.
Hãy trả lời dựa trên nội dung tài liệu được cung cấp.
Nếu không tìm thấy thông tin liên quan, hãy nói rõ bạn không biết.
..."""
```

---

## 5. Service Rules

### 5.1 Service Classes
- All external integrations wrapped in service classes: `PineconeService`, `EmbeddingService`, `LLMService`, `BeCoreClient`
- Services are singletons (instantiated once, injected via DI)
- Services handle retry logic, rate limiting, and error wrapping internally

### 5.2 PineconeService Rules
```python
# ALWAYS pass organization_id as namespace
await pinecone_service.upsert(vectors=..., namespace=f"org_{organization_id}")
await pinecone_service.query(vector=..., namespace=f"org_{organization_id}", filter=...)

# NEVER query without namespace
await pinecone_service.query(vector=...)  # ❌ - exposes all tenants
```

### 5.3 EmbeddingService Rules
- Always batch embeddings (100 texts per API call max)
- Cache embeddings in Redis with key `embed:{sha256(text)[:16]}` — TTL 24h
- Use `text-embedding-3-small` model — never change without updating Pinecone index dimension

### 5.4 BeCoreClient Rules
- All calls to `iccp_be_core` go through `app/clients/be_core_client.py`
- Use `httpx.AsyncClient` with connection pooling (not per-request instances)
- Always set `X-Internal-Key` header from `settings.INTERNAL_API_KEY`
- Retry: 3 attempts, exponential backoff, only on 5xx/network errors
- Timeout: connect=5s, read=30s

---

## 6. Celery / Background Tasks Rules

### 6.1 Task Design
- All ingestion tasks are idempotent (safe to retry)
- Tasks receive only serializable primitives (`str`, `int`, `dict`) — never objects
- Task states must be tracked: `PENDING → STARTED → SUCCESS | FAILURE`

```python
@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def ingest_document_task(self, document_id: str, organization_id: str, file_path: str):
    ...
```

### 6.2 Task Routing
```python
CELERY_TASK_ROUTES = {
    "app.workers.tasks.ingest.*": {"queue": "ingest"},
    "app.workers.tasks.analytics.*": {"queue": "analytics"},
}
```

### 6.3 No Celery in Agent Methods
Agents must be pure async Python — never call Celery tasks from within agents. Route handlers trigger Celery tasks, agents do not.

---

## 7. Database Rules

### 7.1 No Direct Writes to be_core Tables
`iccp_be_ai` must NOT write to any `iccp_be_core` PostgreSQL tables directly. All writes go through be_core's internal API:

```python
# ✅ Correct
await be_core_client.update_document_status(document_id, "indexed")

# ❌ Wrong
await db.execute("UPDATE knowledge.documents SET status='indexed' WHERE id=:id", ...)
```

### 7.2 Local AI Tables
`iccp_be_ai` maintains its own lightweight tables (same PostgreSQL instance, separate schema `ai`):
- `ai.ingest_jobs` — tracks ingestion job status
- `ai.chunk_cache` — optional local chunk metadata cache

Use **SQLAlchemy async** (`asyncpg` driver) for all DB operations in this service.

### 7.3 Migrations
- Use **Alembic** for migrations
- Migration files go in `alembic/versions/`
- Always run migrations in Docker entrypoint before starting the app

---

## 8. Configuration Rules

### 8.1 Settings
All config via `pydantic-settings` in `app/core/config.py`:

```python
class Settings(BaseSettings):
    APP_PORT: int = 8001
    ENVIRONMENT: str = "dev"

    OPENAI_API_KEY: str
    PINECONE_API_KEY: str
    PINECONE_INDEX_NAME: str = "iccp-knowledge"

    INTERNAL_API_KEY: str
    BE_CORE_BASE_URL: str

    POSTGRES_URL: str
    REDIS_URL: str

    model_config = SettingsConfigDict(env_file=".env.dev")
```

### 8.2 Env Files
- `.env.dev` — development (not committed)
- `.env.prod` — production (not committed)
- `.env.example` — committed, shows all required keys without values
- Never hardcode secrets, keys, or URLs in source files

### 8.3 Feature Flags
Experimental features gated by env vars:
```
ENABLE_RERANKING=true       # Cross-encoder re-ranking
ENABLE_QUERY_EXPANSION=false # HyDE / query rewriting
ENABLE_ANALYTICS_AGENT=true
```

---

## 9. Logging Rules

- Use Python `structlog` for structured JSON logs
- Every log entry must include: `trace_id`, `organization_id`, `service="iccp_be_ai"`
- Log levels: `DEBUG` (dev), `INFO` (prod), `ERROR` always
- Never log: API keys, passwords, full document content, PII

```python
log.info("document.ingested", document_id=doc_id, chunks=len(chunks), duration_ms=elapsed)
log.error("pinecone.upsert_failed", document_id=doc_id, error=str(e))
```

---

## 10. Docker Rules

### 10.1 Dockerfile Stages
- `base` — Python deps install
- `dev` — hot reload with `uvicorn --reload`
- `prod` — optimized, gunicorn with uvicorn workers

### 10.2 Docker Compose
- `docker-compose.yaml` — dev (with volume mounts for hot reload)
- `docker-compose.prod.yaml` — production (no volume mounts)
- Services: `iccp-be-ai`, `redis`, `celery-worker`, `celery-beat` (optional)
- Connect to same Docker network as `iccp_be_core` for internal communication

### 10.3 Health Check
Every service in docker-compose must define `healthcheck`.
The FastAPI app must expose `GET /health` returning `{"status": "ok"}`.

---

## 11. Testing Rules

- Test files mirror source structure: `tests/api/v1/test_ingest.py`
- Unit tests for agents mock external services (Pinecone, OpenAI)
- Integration tests use real services only in CI
- Minimum coverage target: 70% for `app/agents/` and `app/services/`
- Use `pytest` + `pytest-asyncio` + `httpx.AsyncClient` for API tests

---

## 12. Code Quality

- **Formatter:** `black` (line length 88)
- **Linter:** `ruff`
- **Type checker:** `mypy` (strict mode for `app/core/` and `app/agents/`)
- All functions must have type annotations
- No `Any` types except in external API response parsing
- Max function length: 50 lines. If longer, split into helpers.
