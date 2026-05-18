# Knowledge Base — Mục lục

> Đây là mục lục điều hướng cho toàn bộ tài liệu thiết kế hệ thống RAG trong `iccp_be_ai`.

---

## Điều hướng nhanh

| Tài liệu | Mô tả | Đọc khi nào |
|---|---|---|
| [README.md](./README.md) | Tổng quan, quy trình cập nhật docs, phân biệt 2 hệ thống status | Đọc đầu tiên |
| [hybrid-rag-plan.md](./hybrid-rag-plan.md) | Kiến trúc đầy đủ, pipeline, contracts, OCR, OpenSearch schema, slots POC | Trước khi implement |
| [kb-index.md](./kb-index.md) | Danh sách tài liệu curated đang quản lý trong KB | Khi thêm/cập nhật tài liệu |
| [domains/general-index.md](./domains/general-index.md) | Entity, relation, ACL rule cho domain `general` | Khi implement graph extraction |

---

## Cấu trúc thư mục

```
docs/knowledge-base/
├── INDEX.md                     ← file này (mục lục)
├── README.md                    ← hướng dẫn, quy trình, phân biệt status
├── hybrid-rag-plan.md           ← kiến trúc + contracts + ke hoach trien khai
├── kb-index.md                  ← catalog tài liệu curated
└── domains/
    └── general-index.md         ← domain general: entity, relation, ACL
```

---

## Tóm tắt kiến trúc hybrid RAG

```
User message (+ảnh?)
      │
      ▼
OrchestratorInput { org_id, user_id, role_ids, context_scope, context_id, image_text? }
      │
      ├─ OCR (nếu user gửi ảnh) → image_text
      │
      ▼
RouterAgent → intent: RAG / WEB / HYBRID / CHITCHAT
      │
      ├─ mode=rag/hybrid ──────────────────────────────────────────────┐
      │                                                                  │
      │  OpenSearch (BM25)             Pinecone (vector semantic)        │
      │  filter: org_id + scope        filter: org_id + scope + project  │
      │           ↓                              ↓                       │
      │     top-K lexical              top-K vector                      │
      │                └──────────────┬──────────┘                      │
      │                               ▼                                  │
      │                   RRF Fusion (Reciprocal Rank Fusion)            │
      │                               ▼                                  │
      │   POST /internal/documents/batch-access-check (be_core)         │
      │   → verify từng document_id với user live DB                    │
      │   → loại bỏ chunks bị revoke quyền                              │
      │                               ▼                                  │
      │                   Graph Rerank (score_graph từ PostgreSQL)       │
      │                               ▼                                  │
      │                   Chunks đã qua runtime ACL  ←──────────────────┘
      │
      ├─ mode=web ──→ WebSearchAgent (DuckDuckGo)
      │
      ▼
ChatAgent { chunks + web_sources + image_text + history } → LLM stream
      │
      ▼
SSE tokens + citations { score_lexical, score_vector, score_graph, final_score }
```

---

## Stack công nghệ

| Layer | Công nghệ | Host | Ghi chú |
|---|---|---|---|
| Vector store | **Pinecone** | Managed cloud | Namespace per org: `org_{org_id}` |
| Lexical store | **OpenSearch 2.13** | Self-hosted Docker | BM25, index per org: `iccp_documents_{org_id}` |
| Graph store | **PostgreSQL** (custom tables) | be_core PG | 3 bảng schema `knowledge`: entities, relations, chunk_links |
| Embedding | **Gemini text-embedding-004** | Google API | 768 dims, Redis cache 24h |
| Chat LLM | **Gemini gemini-2.5-flash** | Google API | Streaming SSE |
| Queue | **Celery + Redis** | Self-hosted Docker | Queue: `ingest`, `analytics` |
| DB hội thoại | **MongoDB** | Self-hosted Docker | Conversations, messages, quota, ingest jobs |
| OCR | **Tesseract** | In-process | Document ingestion + chat image |
| Content policy | **ContentPolicyService** | In-process | Gate bắt buộc trước khi index |

---

## Status lifecycle

### be_core DocumentStatus (enum kỹ thuật)
```
not_indexed → pending → processing → indexed → failed
```
Được quản lý bởi be_core (upload/new_version) + be_ai Celery worker (processing/indexed/failed).

### Lifecycle events ảnh hưởng đến vector store

| Sự kiện be_core | Pinecone/OpenSearch | Hành động bắt buộc |
|---|---|---|
| Upload mới | Không có chunks | Trigger chunking (manual hoặc auto) |
| Upload version mới | Chunks cũ vẫn còn | **Xóa chunks cũ** → index lại toàn bộ |
| Soft delete (`isActive=false`) | Chunks vẫn còn | `batch-access-check` block qua `isActive` check |
| Hard delete (permanent) | Chunks vẫn còn 🚨 | **be_core gọi be_ai xóa vectors ngay** |
| Update `accessScope`/`folderId` | Metadata stale 🚨 | **be_core gọi be_ai sync metadata** |
| Restore từ recovery | Chunks có thể còn | Kiểm tra status, re-index nếu cần |

### KB tracking status (chỉ dùng trong kb-index.md)
Cho tài liệu curated thêm thủ công — xem [README.md](./README.md) để phân biệt.

---

## Slots implement (thứ tự ưu tiên)

> Xem chi tiết từng slot tại `docs/knowledge-base/implement/`.  
> **Phân biệt:** "Code done" = code đã viết trong repo. "Blueprint" = chỉ có tài liệu hướng dẫn.

| Slot | Nội dung | Status | Ghi chú |
|---|---|---|---|
| **Slot 1** | Internal routes be_core (`/internal/documents/*`) | 🔴 Blueprint | `implement/01-be-core-internal-routes.md` viết xong, chưa có code trong be_core |
| **Slot 2** | File parser: OCR Tesseract + Excel + file type enum | 🟡 Code done (chưa deploy) | `FileParserService` refactored, Dockerfile + requirements updated |
| **Slot 3** | OpenSearch: lexical index + dual-index ingestion | 🔴 Blueprint | `implement/03-opensearch-service.md` — chưa có code |
| **Slot 4** | Runtime ACL: role_ids/project_ids, RRF fusion, batch-access-check | 🔴 Blueprint | `implement/04-acl-retrieval.md` — phụ thuộc Slot 1 + 3 |
| **Slot 5** | Document lifecycle: hard delete → delete vectors, metadata sync | 🔴 Blueprint | `implement/05-document-lifecycle.md` — phụ thuộc Slot 1 |
| **Slot 6** | FE: trigger chunking button, document-scoped chat, citation UI | 🔴 Blueprint | `implement/06-web-integration.md` — FE còn là placeholder |
| **Slot 7** | Image chat: Gemini Vision + image field MessageSchema | 🟡 Schema done (chưa deploy) | `MessageSchema` + `SendMessageRequest` + `GeminiVisionService` docs xong, endpoint chưa wire |
| **Slot 8** | Validation defense-in-depth: MIME/size ở be_core + be_ai + FE | ✅ Code done | `FileTypeValidator` be_core, `AllowedFileType` Literal be_ai, `validateDocumentFile()` FE — cần rebuild |
| **Slot 9** | Graph linking + rerank (PostgreSQL graph tables) | 🔴 Blueprint | `implement/05` mô tả sơ bộ, chưa có schema migration |
| **Slot 10** | E2E test, benchmark recall@10 | 🔴 Chưa bắt đầu | |

### Legend

| Icon | Nghĩa |
|---|---|
| ✅ Code done | Code đã viết, đã test thủ công, sẵn sàng deploy |
| 🟡 Code done (chưa deploy) | Code đã viết nhưng chưa build/restart Docker — cần `docker-compose up --build` |
| 🔴 Blueprint | Chỉ có tài liệu thiết kế, chưa có code implement |
