# ICCP AI Service — Project Structure

## Root Layout

```
iccp_be_ai/
├── app/                          # Main application package
│   ├── __init__.py
│   ├── main.py                   # FastAPI app factory, middleware, lifespan
│   │
│   ├── core/                     # App-wide config & infrastructure
│   │   ├── __init__.py
│   │   ├── config.py             # pydantic-settings: all env vars
│   │   ├── logging.py            # structlog setup, JSON formatter
│   │   ├── exceptions.py         # Custom exception classes
│   │   ├── dependencies.py       # Shared FastAPI Depends() (auth, db, services)
│   │   └── security.py           # JWT decode, internal API key verification
│   │
│   ├── api/                      # HTTP layer
│   │   ├── __init__.py
│   │   └── v1/
│   │       ├── __init__.py
│   │       ├── router.py         # Aggregates all v1 sub-routers
│   │       ├── health.py         # GET /health
│   │       ├── ingest.py         # POST /ingest/documents, DELETE /ingest/documents/{id}
│   │       └── chat.py           # POST /chat/conversations, POST /chat/conversations/{id}/messages, GET history
│   │
│   ├── agents/                   # Multi-agent layer (LangGraph)
│   │   ├── __init__.py
│   │   ├── base.py               # BaseAgent abstract class, AgentInput/AgentOutput models
│   │   ├── orchestrator.py       # AgentOrchestrator: entry point for all agent flows
│   │   ├── router_agent.py       # RouterAgent: intent classification, routing decision
│   │   ├── ingestion_agent.py    # IngestionAgent: parse → chunk → embed → upsert Pinecone
│   │   ├── retrieval_agent.py    # RetrievalAgent: embed query → Pinecone search → ranked chunks
│   │   ├── chat_agent.py         # ChatAgent: build prompt → call LLM → stream → citations
│   │   └── analytics_agent.py   # AnalyticsAgent: async, records usage metrics
│   │
│   ├── prompts/                  # All LLM prompt templates
│   │   ├── __init__.py
│   │   ├── rag_chat.py           # System prompt for RAG chat (Vietnamese)
│   │   ├── intent_router.py      # Prompt for intent classification
│   │   └── chitchat.py           # Direct conversation prompt
│   │
│   ├── services/                 # External integrations (single responsibility each)
│   │   ├── __init__.py
│   │   ├── pinecone_service.py   # Pinecone: upsert, query, delete by filter
│   │   ├── embedding_service.py  # OpenAI embeddings: batch embed, Redis cache
│   │   ├── llm_service.py        # OpenAI chat: invoke, stream, token tracking
│   │   ├── chunking_service.py   # Text parsing + RecursiveCharacterTextSplitter
│   │   └── file_parser_service.py # PDF (PyMuPDF), DOCX (python-docx), TXT, MD parsers
│   │
│   ├── clients/                  # HTTP clients for internal service communication
│   │   ├── __init__.py
│   │   └── be_core_client.py     # httpx.AsyncClient wrapper for iccp_be_core internal API
│   │
│   ├── db/                       # Local database (ai schema only)
│   │   ├── __init__.py
│   │   ├── session.py            # SQLAlchemy async engine + session factory
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   ├── ingest_job.py     # ai.ingest_jobs table: job status tracking
│   │   │   └── base.py           # DeclarativeBase
│   │   └── repositories/
│   │       ├── __init__.py
│   │       └── ingest_job_repo.py # CRUD for ingest_jobs
│   │
│   ├── schemas/                  # Pydantic request/response models
│   │   ├── __init__.py
│   │   ├── common.py             # Shared: PaginatedResponse, ErrorResponse, TokenPayload
│   │   ├── ingest.py             # IngestDocumentRequest, IngestJobResponse, IngestStatusUpdate
│   │   └── chat.py               # CreateConversationRequest, SendMessageRequest, MessageResponse, ChatStreamChunk
│   │
│   └── workers/                  # Celery async task workers
│       ├── __init__.py
│       ├── celery_app.py         # Celery instance, broker/backend config, task routing
│       └── tasks/
│           ├── __init__.py
│           ├── ingest_tasks.py   # ingest_document_task (idempotent, retryable)
│           └── analytics_tasks.py # record_chat_analytics_task
│
├── alembic/                      # DB migrations (ai schema only)
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_create_ai_schema.py
│
├── tests/                        # Test suite
│   ├── __init__.py
│   ├── conftest.py               # Fixtures: test app, mock services, test DB
│   ├── unit/
│   │   ├── agents/
│   │   │   ├── test_ingestion_agent.py
│   │   │   ├── test_retrieval_agent.py
│   │   │   ├── test_chat_agent.py
│   │   │   └── test_router_agent.py
│   │   └── services/
│   │       ├── test_chunking_service.py
│   │       ├── test_embedding_service.py
│   │       └── test_pinecone_service.py
│   └── integration/
│       ├── test_ingest_api.py
│       └── test_chat_api.py
│
├── Dockerfile                    # Multi-stage: base, dev, prod
├── docker-compose.yaml           # Dev: app + redis + celery-worker
├── docker-compose.prod.yaml      # Prod: optimized, no volume mounts
├── alembic.ini                   # Alembic config
├── requirements.txt              # Production dependencies (pinned)
├── requirements-dev.txt          # Dev dependencies (pytest, black, ruff, mypy)
├── pyproject.toml                # black, ruff, mypy, pytest config
├── .env.example                  # All env vars (no values)
└── README.md                     # Setup & run instructions
```

---

## Detailed File Responsibilities

### `app/main.py`
```python
# Responsibilities:
# - Create FastAPI app instance
# - Register all middleware: CORS, GZip, RequestID, logging
# - Register global exception handlers
# - Lifespan: initialize Pinecone client, httpx client, Redis pool on startup
# - Include v1 router
# - Mount /docs (Swagger) and /redoc
```

### `app/core/config.py`
```python
# All settings from environment:
# APP_PORT, ENVIRONMENT, LOG_LEVEL
# OPENAI_API_KEY, OPENAI_EMBEDDING_MODEL, OPENAI_CHAT_MODEL
# PINECONE_API_KEY, PINECONE_INDEX_NAME, PINECONE_ENVIRONMENT
# POSTGRES_URL (asyncpg)
# REDIS_URL
# BE_CORE_BASE_URL, INTERNAL_API_KEY
# JWT_SECRET (same as be_core for token verification)
# CELERY_BROKER_URL, CELERY_RESULT_BACKEND
# CHUNK_SIZE (default: 512), CHUNK_OVERLAP (default: 64)
# RETRIEVAL_TOP_K (default: 6)
# ENABLE_RERANKING, ENABLE_QUERY_EXPANSION
```

### `app/agents/orchestrator.py`
```python
# Responsibilities:
# - Build LangGraph StateGraph
# - Nodes: router_node, retrieval_node, chat_node, analytics_node
# - Edges: router → (retrieval → chat) | (chat direct) → analytics
# - astream_events() for streaming
# - Handle tenant context injection into every node
```

### `app/agents/ingestion_agent.py`
```python
# Responsibilities:
# - Accept: document_id, organization_id, file_path, file_type, access_scope, project_id
# - Call FileParserService → raw text
# - Call ChunkingService → List[Chunk]
# - Call EmbeddingService → List[float] per chunk (batched)
# - Call PineconeService.upsert() with namespace=f"org_{organization_id}"
# - Return: IngestionResult(chunks_count, status)
# Note: Does NOT update DB directly — caller does that
```

### `app/agents/retrieval_agent.py`
```python
# Responsibilities:
# - Accept: query, organization_id, top_k, access_filter (scope + project_id + role_ids)
# - Call EmbeddingService.embed(query)
# - Build Pinecone metadata filter for tenant + access scope
# - Call PineconeService.query()
# - Optional: cross-encoder re-ranking if ENABLE_RERANKING=true
# - Return: List[RetrievedChunk(content, score, document_id, chunk_index, metadata)]
```

### `app/agents/chat_agent.py`
```python
# Responsibilities:
# - Accept: conversation_id, user_message, retrieved_chunks, history, organization_id
# - Build ChatPromptTemplate with system prompt + context + history
# - Call LLMService.astream() → yields tokens
# - Accumulate full response, extract citation references
# - Return: ChatAgentOutput(response_text, citations, tokens_used)
```

### `app/services/pinecone_service.py`
```python
# Methods:
# - async upsert(vectors: List[PineconeVector], namespace: str) → None
# - async query(vector: List[float], namespace: str, filter: dict, top_k: int) → List[ScoredVector]
# - async delete_by_filter(namespace: str, filter: dict) → None
# - async delete_namespace(namespace: str) → None
# Internals:
# - Uses pinecone-client SDK (async)
# - Batches upserts: 100 vectors per call
# - Handles Pinecone exceptions → raises VectorStoreException
```

### `app/services/chunking_service.py`
```python
# Methods:
# - chunk_text(text: str, metadata: dict) → List[Chunk]
# Uses:
# - langchain.text_splitter.RecursiveCharacterTextSplitter
# - chunk_size=settings.CHUNK_SIZE (512 tokens)
# - chunk_overlap=settings.CHUNK_OVERLAP (64 tokens)
# - Separators: ["\n\n", "\n", "。", ".", " "] (Vietnamese-aware)
```

### `app/services/file_parser_service.py`
```python
# Methods:
# - parse(file_path: str, file_type: str) → str
# Supported:
# - PDF → PyMuPDF (fitz) — preserves paragraph structure
# - DOCX → python-docx — extracts paragraphs + tables
# - TXT, MD → plain read with encoding detection (chardet)
# - Future: XLSX (openpyxl), HTML (BeautifulSoup)
```

### `app/clients/be_core_client.py`
```python
# Wraps all iccp_be_core internal API calls:
# - async update_document_status(document_id, status, error_msg=None)
# - async save_document_chunks(document_id, chunks: List[ChunkData])
# - async save_message(conversation_id, role, content, metadata)
# - async save_citations(message_id, citations: List[CitationData])
# - async record_analytics(payload: AnalyticsPayload)
# - async get_document_info(document_id) → DocumentInfo
# All calls include: X-Internal-Key header, timeout, retry on 5xx
```

### `app/workers/tasks/ingest_tasks.py`
```python
# Tasks:
# - ingest_document_task(document_id, organization_id, file_path, file_type, access_scope, project_id)
#   1. Mark job STARTED in ai.ingest_jobs
#   2. Run IngestionAgent.run()
#   3. Call be_core_client.save_document_chunks()
#   4. Call be_core_client.update_document_status("indexed")
#   5. Mark job SUCCESS
#   On failure: retry up to 3x, then mark FAILED + update doc status "failed"
```

---

## Database Schema (ai schema — local to iccp_be_ai)

```sql
CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE ai.ingest_jobs (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id   UUID NOT NULL,
  organization_id UUID NOT NULL,
  status        VARCHAR(20) NOT NULL DEFAULT 'pending', -- pending, started, success, failed
  chunks_count  INT,
  error_message TEXT,
  started_at    TIMESTAMP,
  completed_at  TIMESTAMP,
  created_at    TIMESTAMP DEFAULT NOW(),
  updated_at    TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_ingest_jobs_document_id ON ai.ingest_jobs(document_id);
CREATE INDEX idx_ingest_jobs_org_status  ON ai.ingest_jobs(organization_id, status);
```

---

## Pinecone Vector Metadata Schema

Every vector upserted to Pinecone carries this metadata (used for filtering):

```json
{
  "document_id":      "uuid-string",
  "organization_id":  "uuid-string",
  "chunk_index":      0,
  "content":          "The actual text of this chunk...",
  "file_name":        "hr_policy_2024.pdf",
  "file_type":        "pdf",
  "access_scope":     "organization",
  "project_id":       "uuid-string or null",
  "role_ids":         ["uuid1", "uuid2"],
  "token_count":      128,
  "indexed_at":       "2024-01-15T08:30:00Z"
}
```

---

## Environment Variables (`.env.example`)

```env
# App
APP_PORT=8001
ENVIRONMENT=dev
LOG_LEVEL=INFO

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
OPENAI_CHAT_MODEL=gpt-4o-mini
OPENAI_MAX_TOKENS=2048

# Pinecone
PINECONE_API_KEY=pcsk_...
PINECONE_INDEX_NAME=iccp-knowledge
PINECONE_ENVIRONMENT=us-east-1-aws

# PostgreSQL (shared with be_core)
POSTGRES_URL=postgresql+asyncpg://user:pass@postgres:5432/iccp_db

# Redis
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2

# Internal communication
BE_CORE_BASE_URL=http://iccp-be-core:3000
INTERNAL_API_KEY=super-secret-internal-key

# JWT (same secret as be_core)
JWT_SECRET=your-jwt-secret
JWT_ALGORITHM=HS256

# RAG tuning
CHUNK_SIZE=512
CHUNK_OVERLAP=64
RETRIEVAL_TOP_K=6

# Feature flags
ENABLE_RERANKING=false
ENABLE_QUERY_EXPANSION=false
ENABLE_ANALYTICS_AGENT=true
```

---

## Docker Services

### `docker-compose.yaml` (dev)

```yaml
services:
  iccp-be-ai:          # FastAPI with uvicorn --reload
  celery-worker:       # Celery worker (queue: ingest, analytics)
  redis:               # Broker + cache + result backend
  # postgres: shared with iccp_be_core network — NOT re-declared here
```

### `docker-compose.prod.yaml` (prod)

```yaml
services:
  iccp-be-ai:          # Gunicorn + uvicorn workers (4 workers)
  celery-worker:       # Celery worker with concurrency=4
  celery-beat:         # Celery scheduler (optional analytics aggregation)
  redis:               # Prod Redis
```

### Network
Both `iccp_be_core` and `iccp_be_ai` must share the same Docker external network `iccp-network` so they can communicate via container names.

---

## Key Dependencies (`requirements.txt`)

```
fastapi==0.115.x
uvicorn[standard]==0.32.x
gunicorn==23.x

# AI / RAG
openai==1.x
langchain==0.3.x
langchain-openai==0.2.x
langgraph==0.2.x
pinecone-client==5.x

# File parsing
pymupdf==1.24.x          # PDF
python-docx==1.1.x       # DOCX
chardet==5.x             # Encoding detection

# DB
sqlalchemy[asyncio]==2.x
asyncpg==0.30.x
alembic==1.13.x

# Background tasks
celery[redis]==5.4.x

# HTTP client
httpx==0.27.x

# Config & validation
pydantic==2.x
pydantic-settings==2.x

# Logging
structlog==24.x

# Caching
redis==5.x
```

```
# requirements-dev.txt
pytest==8.x
pytest-asyncio==0.24.x
pytest-cov==5.x
httpx==0.27.x          # for AsyncClient in tests
black==24.x
ruff==0.6.x
mypy==1.11.x
```
