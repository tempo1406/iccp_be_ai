# ICCP AI Service — Giải thích toàn bộ luồng hoạt động

## 1. Tổng quan kiến trúc

```
[Client / Browser]
        │
        ▼
[iccp_be_core - NestJS]  ←──────────────────────────────────┐
   - Xác thực (JWT)                                          │
   - Quản lý tài liệu (upload file)                         │
   - Quản lý conversation, messages                         │
   - Webhook → gọi iccp_be_ai khi doc sẵn sàng             │
        │                                                    │
        │ HTTP (internal)                                    │ HTTP (internal)
        ▼                                                    │
[iccp_be_ai - FastAPI]  ─────────────────────────────────────┘
   - Nhận ingestion job
   - Xử lý RAG pipeline
   - Trả lời chat (streaming SSE)
        │
        ├──→ [Pinecone]   ← lưu / tìm kiếm vector
        ├──→ [OpenAI]     ← tạo embedding + sinh text
        ├──→ [Redis]      ← cache embedding, Celery broker
        └──→ [PostgreSQL] ← lưu ingest_jobs (schema ai)
```

---

## 2. Luồng 1 — Ingestion (Xử lý tài liệu)

### 2.1 Ai trigger?
`iccp_be_core` gọi `POST /api/v1/ingest/documents` sau khi user upload file.

### 2.2 Luồng code chi tiết

```
[iccp_be_core]
    POST /api/v1/ingest/documents
    Body: { document_id, organization_id, file_path, file_name, file_type, access_scope }
    Header: X-Internal-Key: <internal_key>
          │
          ▼
[app/api/v1/ingest.py] → ingest_document()
    1. Verify X-Internal-Key (Depends(verify_internal_request))
    2. Tạo record IngestJob trong bảng ai.ingest_jobs (status="pending")
    3. Gọi Celery task: ingest_document_task.apply_async(...)
    4. Trả về 202 Accepted + job_id ngay lập tức
          │
          ▼ (async, background)
[app/workers/tasks/ingest_tasks.py] → ingest_document_task()
    1. Đổi status IngestJob → "started"
    2. Gọi IngestionAgent.run(IngestionInput)
          │
          ▼
[app/agents/ingestion_agent.py] → IngestionAgent.run()
    Step 1: FileParserService.parse(file_path, file_type)
            │
            ├── PDF  → PyMuPDF (fitz): đọc từng trang, trả về text có [Page N]
            ├── DOCX → python-docx: đọc paragraphs + tables
            └── TXT/MD → chardet detect encoding → decode → plain text
            │
            ▼
    Step 2: ChunkingService.chunk_text(raw_text, metadata)
            - Normalize text (remove extra whitespace, control chars)
            - RecursiveCharacterTextSplitter:
              chunk_size = CHUNK_SIZE * 4 chars (~512 tokens)
              chunk_overlap = CHUNK_OVERLAP * 4 chars (~64 tokens)
              separators = ["\n\n", "\n", "。", ".", "!", "?", " ", ""]
            - Trả về List[Chunk(chunk_index, content, token_count, metadata)]
            │
            ▼
    Step 3: EmbeddingService.embed_batch(texts)
            - Với mỗi chunk.content:
              1. Tính SHA256(text)[:24] → cache_key
              2. Check Redis: nếu có → dùng luôn (TTL 24h)
              3. Nếu không → gọi OpenAI API (text-embedding-3-small, dim=1536)
              4. Batch tối đa 100 texts/call
              5. Lưu kết quả vào Redis
            - Trả về List[List[float]] (1536 dims mỗi vector)
            │
            ▼
    Step 4: Build PineconeVector cho mỗi chunk
            id = "{document_id}_{chunk_index}"
            values = embedding vector (1536 floats)
            metadata = {
              document_id, organization_id, chunk_index,
              content (text gốc), file_name, file_type,
              access_scope, project_id, role_ids,
              token_count, indexed_at
            }
            │
            ▼
    Step 5: PineconeService.upsert(vectors, namespace="org_{organization_id}")
            - NAMESPACE = "org_{organization_id}" → cô lập hoàn toàn giữa các tenant
            - Batch upsert: 100 vectors/lần
            - Dùng asyncio.to_thread() để không block event loop
          │
          ▼
[app/workers/tasks/ingest_tasks.py] tiếp tục
    3. BeCoreClient.save_document_chunks(document_id, chunks)
       → PATCH /internal/documents/{id}/chunks tại iccp_be_core
       → be_core lưu vào bảng knowledge.document_chunks
    4. BeCoreClient.update_document_status(document_id, "indexed")
       → PATCH /internal/documents/{id}/status
    5. Đổi status IngestJob → "success", lưu chunks_count

    Nếu lỗi ở bất kỳ bước nào:
    - Celery retry tối đa 3 lần (mỗi lần cách 60s)
    - Sau 3 lần fail: IngestJob → "failed", document status → "failed"
```

---

## 3. Luồng 2 — Chat (Hỏi đáp RAG)

### 3.1 Ai trigger?
Client (browser) gọi `POST /api/v1/chat/conversations/{id}/messages` với JWT token.

### 3.2 Luồng code chi tiết

```
[Client / Browser]
    POST /api/v1/chat/conversations/{conversation_id}/messages
    Body: { content: "Quy trình nghỉ phép là gì?" }
    Header: Authorization: Bearer <jwt_token>
          │
          ▼
[app/api/v1/chat.py] → send_message()
    1. Decode JWT → TokenPayload { user_id, organization_id }
    2. Tạo OrchestratorInput
    3. Gọi AgentOrchestrator.stream(input) → StreamingResponse (SSE)
          │
          ▼
[app/agents/orchestrator.py] → AgentOrchestrator.stream()

    ┌─────────────────────────────────────────────────────────┐
    │ BƯỚC 1: RouterAgent                                     │
    │ RouterAgent.run(RouterInput)                            │
    │   → Gọi LLM (GPT-4o-mini) với prompt phân loại intent  │
    │   → Trả về: DOCUMENT_QUERY | TASK_QUERY | CHITCHAT     │
    └──────────────────────┬──────────────────────────────────┘
                           │
               ┌───────────┴────────────┐
               ▼                        ▼
    [DOCUMENT/TASK_QUERY]          [CHITCHAT]
               │                        │
               ▼                        │
    ┌──────────────────────┐            │
    │ BƯỚC 2: RetrievalAgent│           │
    │  1. EmbeddingService. │           │
    │     embed_one(query)  │           │
    │     (check Redis cache│           │
    │      trước)           │           │
    │  2. Build filter:     │           │
    │   { organization_id:  │           │
    │     {$eq: org_id},    │           │
    │     $or: [scope filter│           │
    │     + project filter] │           │
    │   }                   │           │
    │  3. PineconeService.  │           │
    │     query(vector,     │           │
    │     namespace=        │           │
    │     "org_{org_id}",   │           │
    │     filter, top_k=6)  │           │
    │  4. Trả về top-K      │           │
    │     RetrievedChunk    │           │
    │     (content, score,  │           │
    │     file_name...)     │           │
    └──────────┬───────────┘            │
               │                        │
               ▼                        ▼
    ┌──────────────────────────────────────────┐
    │ BƯỚC 3: Load conversation history        │
    │  BeCoreClient.get_conversation_messages  │
    │  → GET /internal/conversations/{id}/     │
    │    messages?limit=10                     │
    │  → Lấy 10 message gần nhất              │
    └──────────────────────┬───────────────────┘
                           │
                           ▼
    ┌──────────────────────────────────────────┐
    │ BƯỚC 4: ChatAgent.stream_response()      │
    │                                          │
    │  mode="rag": Build prompt =              │
    │    SystemMessage(RAG_SYSTEM_PROMPT       │
    │      với context = top-K chunks gộp lại) │
    │    + HumanMessage/AIMessage (history)    │
    │    + HumanMessage(user_message)          │
    │                                          │
    │  mode="direct" (chitchat):               │
    │    SystemMessage(CHITCHAT_SYSTEM_PROMPT) │
    │    + HumanMessage(user_message)          │
    │                                          │
    │  → LLMService.astream(messages)          │
    │    → GPT-4o-mini streaming               │
    │    → yield token by token                │
    └──────────────────────┬───────────────────┘
                           │ stream tokens
                           ▼
    ┌──────────────────────────────────────────┐
    │ Orchestrator yields SSE events:          │
    │  {"type":"token","content":"Quy..."}     │
    │  {"type":"token","content":" trình..."}  │
    │  ...                                     │
    │  {"type":"done","message_id":"...","     │
    │   citations":[{doc,chunk,score,...}]}    │
    └──────────────────────┬───────────────────┘
                           │
                           ▼
    ┌──────────────────────────────────────────┐
    │ BƯỚC 5 (sau khi stream xong):            │
    │  BeCoreClient.save_message(user msg)     │
    │  BeCoreClient.save_message(assistant)    │
    │  BeCoreClient.save_citations(...)        │
    └──────────────────────┬───────────────────┘
                           │
                           ▼
    ┌──────────────────────────────────────────┐
    │ BƯỚC 6: AnalyticsAgent (fire & forget)   │
    │  asyncio.create_task(analytics.run())    │
    │  → BeCoreClient.record_analytics(...)   │
    │  → POST /internal/analytics/chat        │
    └──────────────────────────────────────────┘
```

### 3.3 SSE Stream về client
```
Client nhận:
data: {"type": "token", "content": "Nhân"}
data: {"type": "token", "content": " viên"}
data: {"type": "token", "content": " được"}
...
data: {"type": "done", "message_id": "uuid", "citations": [...], "response_time_ms": 1250}
```

---

## 4. Luồng khởi động service (Startup)

```
Docker → uvicorn app.main:app
          │
          ▼
[app/main.py] → lifespan()
    1. setup_logging()           → cấu hình structlog JSON
    2. PineconeService.initialize()
       - Kết nối Pinecone SDK
       - Kiểm tra index "iccp-knowledge" tồn tại chưa
       - Nếu chưa: tạo mới (dim=1536, metric=cosine, serverless)
    3. BeCoreClient.initialize()
       - Tạo httpx.AsyncClient pool
       - Base URL = BE_CORE_BASE_URL
       - Default header: X-Internal-Key
    4. Tạo Redis connection (aioredis)
    5. Log "iccp_be_ai.ready"

→ FastAPI bắt đầu nhận request
```

---

## 5. Luồng Celery Worker

```
celery -A app.workers.celery_app.celery_app worker

Khởi động:
  - Kết nối Redis broker (CELERY_BROKER_URL = redis://redis:6379/1)
  - Lắng nghe queue: "ingest" và "analytics"

Khi nhận task ingest_document_task:
  1. Tạo asyncio event loop mới (Celery chạy sync, ta dùng loop.run_until_complete)
  2. Gọi PineconeService.initialize() (idempotent)
  3. Gọi BeCoreClient.initialize() (idempotent)
  4. Chạy toàn bộ IngestionAgent pipeline (xem mục 2.2)
  5. Celery retry: nếu exception → thử lại tối đa 3 lần
```

---

## 6. Luồng xóa tài liệu

```
[iccp_be_core] user xóa document
    DELETE /api/v1/ingest/documents/{document_id}
    Body: { organization_id }
    Header: X-Internal-Key
          │
          ▼
[app/api/v1/ingest.py]
    PineconeService.delete_by_filter(
        namespace = "org_{organization_id}",
        filter = {"document_id": {"$eq": document_id}}
    )
    → Xóa toàn bộ vectors của document đó trong Pinecone
    → Trả về 204 No Content
```

---

## 7. Cô lập multi-tenant (Tenant Isolation)

Đây là rule quan trọng nhất của hệ thống:

```
Mỗi tổ chức (organization) có NAMESPACE riêng trong Pinecone:
  org_abc123   ← chỉ chứa data của organization "abc123"
  org_def456   ← chỉ chứa data của organization "def456"

Khi query:
  PineconeService.query(
      vector = ...,
      namespace = f"org_{user.organization_id}",   ← bắt buộc
      filter = {"organization_id": {"$eq": org_id}} ← double-check
  )

→ Không bao giờ có thể cross-tenant data leakage
→ JWT decode → organization_id → mọi thao tác đều bị scope vào org đó
```

---

## 8. Sơ đồ các file và vai trò

```
app/
├── main.py              ← Khởi động, middleware, exception handlers
├── core/
│   ├── config.py        ← Đọc tất cả env vars (pydantic-settings)
│   ├── security.py      ← Decode JWT, verify internal key
│   ├── dependencies.py  ← CurrentUser, DBSession, InternalRequest (FastAPI DI)
│   ├── exceptions.py    ← Các loại lỗi có type
│   └── logging.py       ← structlog JSON với trace_id tự động
│
├── api/v1/
│   ├── ingest.py        ← Nhận job từ be_core → queue Celery
│   └── chat.py          ← Nhận message từ client → stream SSE
│
├── agents/
│   ├── orchestrator.py  ← "Não" điều phối toàn bộ flow chat
│   ├── router_agent.py  ← Phân loại intent bằng LLM
│   ├── retrieval_agent.py ← Tìm kiếm Pinecone
│   ├── chat_agent.py    ← Sinh câu trả lời (RAG hoặc direct)
│   ├── ingestion_agent.py ← Pipeline xử lý tài liệu
│   └── analytics_agent.py ← Ghi log analytics (fire-forget)
│
├── services/
│   ├── pinecone_service.py  ← Wrapper Pinecone SDK
│   ├── embedding_service.py ← OpenAI embedding + Redis cache
│   ├── llm_service.py       ← OpenAI chat completion + stream
│   ├── chunking_service.py  ← Cắt text thành chunks
│   └── file_parser_service.py ← Đọc PDF/DOCX/TXT
│
├── clients/
│   └── be_core_client.py  ← HTTP client gọi iccp_be_core internal API
│
├── prompts/
│   ├── rag_chat.py        ← System prompt tiếng Việt cho RAG
│   ├── intent_router.py   ← Prompt phân loại intent
│   └── chitchat.py        ← Prompt trả lời casual
│
├── db/
│   ├── session.py         ← SQLAlchemy async engine
│   ├── models/ingest_job.py ← Bảng ai.ingest_jobs
│   └── repositories/      ← CRUD operations
│
└── workers/
    ├── celery_app.py      ← Cấu hình Celery
    └── tasks/
        ├── ingest_tasks.py    ← Task xử lý tài liệu (background)
        └── analytics_tasks.py ← Task ghi analytics (background)
```
