# ICCP AI Service — Idea & Architecture

## 1. Overview

`iccp_be_ai` is a standalone Python/FastAPI microservice that provides **RAG (Retrieval-Augmented Generation)** capabilities and a **multi-agent chatbot** for the ICCP platform. It is the AI brain of the system and communicates with `iccp_be_core` via internal HTTP APIs.

**Core responsibilities:**
- Receive document ingestion jobs from `iccp_be_core` (triggered when a document status changes to `processing`)
- Parse, chunk, and embed document content
- Store vector embeddings in **Pinecone** (NOT pgvector)
- Serve a multi-agent chatbot that retrieves relevant chunks and generates answers using an LLM
- Support **multi-tenant isolation** — every Pinecone namespace is scoped by `organization_id`
- Write back conversation history and citations to `iccp_be_core`'s PostgreSQL via internal APIs

---

## 2. System Context

```
┌──────────────────────────────────────────────────────────────┐
│                       iccp_be_core (NestJS)                  │
│  - Document upload & storage (file path / S3)                │
│  - Auth, RBAC, Tenant, Project management                    │
│  - Webhook: notify iccp_be_ai when doc is ready              │
│  - Store conversation, messages, citations via API           │
└───────────────┬──────────────────────────────────────────────┘
                │  HTTP (internal API)
                ▼
┌──────────────────────────────────────────────────────────────┐
│                    iccp_be_ai (FastAPI)                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────┐ │
│  │ Ingestion  │  │  Retrieval   │  │    Chat              │ │
│  │  Agent     │  │   Agent      │  │    Agent             │ │
│  └─────┬──────┘  └──────┬───────┘  └──────────┬───────────┘ │
│        │                │                      │             │
│        ▼                ▼                      ▼             │
│  ┌──────────────────────────────────────────────────────┐   │
│  │              Agent Orchestrator                      │   │
│  │         (LangGraph / custom router)                  │   │
│  └──────────────────────────────────────────────────────┘   │
│        │                │                      │             │
│        ▼                ▼                      ▼             │
│  ┌──────────┐   ┌───────────────┐   ┌──────────────────┐   │
│  │ Pinecone │   │ OpenAI/Embed  │   │   PostgreSQL     │   │
│  │ (vector) │   │    (LLM)      │   │ (shared w/ core) │   │
│  └──────────┘   └───────────────┘   └──────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

---

## 3. Multi-Agent Architecture

The system uses a **supervisor multi-agent pattern** (LangGraph-based) with the following agents:

### 3.1 Ingestion Agent
**Trigger:** POST `/api/v1/ingest/documents` (called by iccp_be_core webhook)

**Responsibilities:**
1. Download/read file from path or storage URL
2. Parse file content (PDF, DOCX, TXT, MD)
3. Chunk text using **RecursiveCharacterTextSplitter** (chunk_size=512, overlap=64)
4. Generate embeddings via OpenAI `text-embedding-3-small` (1536 dims)
5. Upsert vectors to Pinecone with metadata:
   ```json
   {
     "organization_id": "uuid",
     "document_id": "uuid",
     "chunk_index": 0,
     "content": "chunk text...",
     "file_type": "pdf",
     "access_scope": "organization",
     "project_id": "uuid | null"
   }
   ```
6. Update document status → `indexed` via iccp_be_core internal API
7. Save chunk records to `knowledge.document_chunks` via iccp_be_core API

### 3.2 Retrieval Agent
**Trigger:** Called internally by Chat Agent

**Responsibilities:**
1. Receive user query + context (organization_id, project_id, user roles)
2. Embed query using same model (`text-embedding-3-small`)
3. Build Pinecone filter based on tenant context:
   ```python
   filter = {
     "organization_id": {"$eq": org_id},
     # If scoped to project:
     "$or": [
       {"access_scope": "organization"},
       {"project_id": {"$eq": project_id}}
     ]
   }
   ```
4. Query Pinecone top-K (default K=6) with filter
5. Return ranked chunks with relevance scores and source metadata

### 3.3 Chat Agent (RAG Chain)
**Trigger:** POST `/api/v1/chat/conversations/{id}/messages`

**Responsibilities:**
1. Load conversation history (last N=10 messages) from DB
2. Call Retrieval Agent to get relevant chunks
3. Build prompt with:
   - System instruction (Vietnamese, role-aware)
   - Retrieved context with citations
   - Conversation history
   - User query
4. Call LLM (OpenAI GPT-4o-mini) with streaming support
5. Post-process: extract citations from response
6. Save message + citations to DB via iccp_be_core API
7. Return streaming response to client

### 3.4 Router Agent (Supervisor)
**Responsibilities:**
1. Analyze user query intent
2. Route to appropriate specialized agent:
   - `DOCUMENT_QUERY` → Chat Agent with RAG
   - `CHITCHAT` → Direct LLM response (no retrieval)
   - `TASK_QUERY` → Chat Agent scoped to project docs
3. Enforce tenant permissions before routing

### 3.5 Analytics Agent (Background)
**Trigger:** Async, after each chat response

**Responsibilities:**
1. Record query, response_time_ms, tokens_used, documents_retrieved
2. Update `analytics.chat_analytics` via iccp_be_core API
3. Track popular queries and citation frequency

---

## 4. Pinecone Design

### Index Structure
- **Index name:** `iccp-knowledge`
- **Dimension:** 1536 (OpenAI text-embedding-3-small)
- **Metric:** cosine
- **Pod type:** serverless (starter) or p1.x1 (production)

### Namespace Strategy
- **Namespace = `org_{organization_id}`** → strict tenant isolation
- Each query filters by namespace + metadata for further scoping

### Metadata Schema per Vector
```json
{
  "document_id": "uuid",
  "organization_id": "uuid",
  "chunk_index": 0,
  "content": "plain text of chunk",
  "file_type": "pdf",
  "file_name": "policy.pdf",
  "access_scope": "organization | project | role",
  "project_id": "uuid | null",
  "role_ids": ["uuid"],
  "token_count": 120,
  "indexed_at": "2024-01-01T00:00:00Z"
}
```

---

## 5. Document Processing Pipeline

```
File Upload (be_core)
    │
    ▼
Webhook → iccp_be_ai /ingest/documents
    │
    ▼
Celery Task (async)
    │
    ├── Parse file (PyMuPDF / python-docx / plain text)
    │
    ├── Clean & normalize text
    │
    ├── Chunk (RecursiveCharacterTextSplitter)
    │       chunk_size=512 tokens
    │       chunk_overlap=64 tokens
    │
    ├── Embed (OpenAI text-embedding-3-small)
    │       Batch size = 100 chunks per API call
    │
    ├── Upsert to Pinecone (namespace = org_{id})
    │       Batch size = 100 vectors per upsert
    │
    └── Update document status → "indexed"
        Write chunks to knowledge.document_chunks (via be_core API)
```

---

## 6. Chat Flow

```
User Message
    │
    ▼
Router Agent (intent classification)
    │
    ├── [DOCUMENT_QUERY]
    │       │
    │       ▼
    │   Retrieval Agent
    │       ├── Embed query
    │       ├── Pinecone query (namespace + filter)
    │       └── Return top-K chunks
    │       │
    │       ▼
    │   Chat Agent (RAG)
    │       ├── Build contextual prompt
    │       ├── Call LLM (GPT-4o-mini)
    │       ├── Stream response
    │       └── Save message + citations
    │
    └── [CHITCHAT]
            │
            ▼
        Chat Agent (direct)
            └── Call LLM without retrieval
```

---

## 7. API Communication with iccp_be_core

`iccp_be_ai` is a **consumer** of `iccp_be_core`. It:

| Direction | Endpoint | Purpose |
|-----------|----------|---------|
| core → ai | `POST /api/v1/ingest/documents` | Trigger doc ingestion |
| core → ai | `POST /api/v1/ingest/documents/{id}/retry` | Retry failed ingestion |
| core → ai | `DELETE /api/v1/ingest/documents/{id}` | Delete doc vectors from Pinecone |
| client → ai | `POST /api/v1/chat/conversations` | Create conversation |
| client → ai | `POST /api/v1/chat/conversations/{id}/messages` | Chat (streaming) |
| client → ai | `GET /api/v1/chat/conversations/{id}/messages` | Get history |
| ai → core | `PATCH /internal/documents/{id}/status` | Update index status |
| ai → core | `POST /internal/documents/{id}/chunks` | Save chunk records |
| ai → core | `POST /internal/conversations/{id}/messages` | Save message |
| ai → core | `POST /internal/analytics/chat` | Log analytics |

Authentication between services: **shared internal API key** (passed as `X-Internal-Key` header).

---

## 8. Technology Stack

| Component | Technology | Reason |
|-----------|-----------|--------|
| Web framework | **FastAPI** | Async, fast, auto-docs |
| LLM | **OpenAI GPT-4o-mini** | Cost-effective, Vietnamese support |
| Embeddings | **OpenAI text-embedding-3-small** | 1536 dims, strong multilingual |
| Vector store | **Pinecone** | Managed, scalable, fast similarity search |
| Agent framework | **LangGraph** | Stateful multi-agent orchestration |
| RAG pipeline | **LangChain** | Chains, retrievers, prompt templates |
| Async tasks | **Celery + Redis** | Background doc processing |
| Database | **PostgreSQL** (shared with be_core) | Conversation history via be_core API |
| Caching | **Redis** | Conversation cache, embedding cache |
| File parsing | **PyMuPDF, python-docx, unstructured** | Multi-format support |
| Container | **Docker + Docker Compose** | Same pattern as iccp_be_core |
| Config | **pydantic-settings** | Type-safe env config |
| HTTP client | **httpx** | Async HTTP for internal API calls |

---

## 9. Non-functional Requirements

- **Multi-tenant isolation:** Every Pinecone query strictly filters by `organization_id` namespace. No cross-tenant data leakage.
- **Async processing:** Document ingestion runs in Celery workers, never blocking the API.
- **Streaming:** Chat responses stream via SSE (Server-Sent Events) for real-time UX.
- **Vietnamese language:** System prompts and chunking optimized for Vietnamese text.
- **Performance targets:**
  - Ingestion: < 30s per 50-page PDF
  - Chat response TTFT (time to first token): < 2s
  - Retrieval latency: < 300ms
- **Retry logic:** Celery tasks retry up to 3 times on transient failures.
- **Observability:** Structured JSON logging, correlation IDs per request.
