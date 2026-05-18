# Hybrid RAG Implementation Plan

## Scope va muc tieu
- Xay dung hybrid retrieval cho du lieu noi bo: lexical + vector + graph rerank.
- Ho tro OCR cho 2 ngu canh:
  - OCR tai lieu khi ingestion (file scan / image-based PDF).
  - OCR anh user gui trong chat (inline image query).
- Dam bao tenant isolation va ACL dung scope truoc khi generate.
- Citation co the truy vet ve doc/chunk va nguon OCR.

---

## Kien truc da chot

### Storage / Index roles

| Store | Role |
|---|---|
| **OpenSearch** | Lexical retrieval (BM25) + metadata filtering |
| **Pinecone** | Vector retrieval (semantic) + metadata filtering, namespace = `org_{organization_id}` |
| **PostgreSQL (be_core)** | Graph-table nhe cho entity / relation / chunk links; cung luu `Document`, `DocumentChunk` |
| **MongoDB (be_ai)** | Conversation, messages, quota, ingest jobs |
| **Redis** | Embedding cache (TTL 24h) |

### Embedding model
- Google Gemini `text-embedding-004` — 768 dimensions, cosine metric.

### Chat LLM
- Google Gemini `gemini-2.5-flash` (streaming SSE).

---

## Status lifecycle

### be_core DocumentStatus (enum trong entity)
Day la status ky thuat cua document trong he thong core:

```
not_indexed  →  pending  →  processing  →  indexed  →  failed
```

- `not_indexed`: vua upload, chua gui sang AI.
- `pending`: da trigger chunking, dang cho Celery worker.
- `processing`: Celery worker dang chay.
- `indexed`: lexical + vector da hoan tat.
- `failed`: loi ky thuat hoac policy.

### KB tracking status (chi dung trong kb-index.md cho curated content)
Day la trang thai quan tri cho tai lieu duoc curate thu cong, KHONG phai DocumentStatus enum:

| ingest_status | y nghia |
|---|---|
| `draft` | Moi khai bao, chua san sang ingest |
| `pending` | Da san sang, cho worker xu ly |
| `indexing` | Dang parse/chunk/index lexical+vector |
| `indexed` | Lexical + vector hoan tat |
| `failed` | Loi ky thuat hoac policy |
| `archived` | Ngung su dung, giu de audit |

| graph_status | y nghia |
|---|---|
| `pending` | Chua xu ly graph |
| `linking` | Dang tao entity/relation links |
| `linked` | Graph links hoan tat |
| `skipped` | Bo qua (document khong phu hop graph) |
| `failed` | Loi trong graph extraction/linking |

---

## Ingestion pipeline (voi content policy gate)

```
Upload file → FileParserService.parse()          → raw text + metadata
           → ContentPolicyService.check_document()
                 ├─ PASS  → tiep tuc pipeline
                 └─ FAIL  → status = "policy_rejected", STOP (khong index gi ca)

Sau khi qua policy gate:
           → OCR (neu can, Tesseract)             → text + source_type
           → ChunkingService.chunk()              → []Chunk voi metadata ACL
           → EmbeddingService.embed_batch()       → []vector (768-dim Gemini)
           → XOA CHUNKS CU (neu la re-index / version moi):
                ├─ PineconeService.delete_by_filter({document_id: id})
                └─ OpenSearchService.delete_by_query({document_id: id})
           → PARALLEL INDEX MOI:
                ├─ PineconeService.upsert()       → vector index (namespace org_{org_id})
                └─ OpenSearchService.bulk_index() → lexical index (iccp_documents_{org_id})
           → BeCoreClient.update_status("indexed")
           → BeCoreClient.save_chunks()           → PostgreSQL document_chunks
```

**Quan trong:**
- Content policy check xay ra 1 lan duy nhat tren toan bo document truoc khi chunk.
  Neu fail → ca Pinecone VA OpenSearch deu khong duoc index.
- XOA CHUNKS CU phai chay TRUOC khi upsert moi de tranh stale chunks tu version cu.
- Neu xoa thanh cong nhung index moi fail → document status = "failed", can retry.

## Retrieval pipeline (version implement)

```
1. Query understanding  →  tach keyword + entity seed
2. Candidate retrieval (parallel):
     - OpenSearch top K lexical (BM25, index: iccp_documents_{org_id})
     - Pinecone top K vector (semantic, namespace: org_{org_id})
3. ACL filter som cho tung candidate set:
     - Filter theo organization_id (hard isolation)
     - Filter theo visibility_scope + allowed_*_ids
     - Dung role_ids tu JWT cua user hien tai
4. Fusion:  Reciprocal Rank Fusion (RRF)
5. Graph rerank:  boost chunk co lien ket entity/relation voi query (PostgreSQL graph tables)
6. Generate:  LLM chi nhan chunks da qua ACL
7. Return:  answer + citations (day du score)
```

---

## OCR pipeline

### Case 1: OCR khi ingestion (file scan / image-based PDF)
```
Upload file  →  FileParserService detect mimeType
           →  neu la scan/image-based  →  Tesseract OCR  →  text output
           →  ChunkingService  →  chunk.source_type = "ocr_document"
           →  embed + upsert Pinecone + OpenSearch
```

- Engine: **Tesseract OCR** (tieng Viet + tieng Anh).
- Luon luu `ocr_confidence` trong chunk metadata.
- Pre-process truoc OCR: grayscale, denoise, deskew.
- Threshold: confidence < 0.5 → flag `low_confidence`, khong block nhung warn trong citation.

### Case 2: OCR anh user gui trong chat
```
User gui message kem anh  →  multipart/form-data hoac base64 trong body
                          →  OCRService.extract_text(image)
                          →  ket qua = "image_context" text
                          →  append vao user_message lam additional context
                          →  OrchestratorInput.image_text = image_context
                          →  ChatAgent dung image_context trong prompt
```

- Chi OCR anh, khong embed / index vao Pinecone (anh la query context, khong phai KB).
- Citation cua anh user: `source_type = "ocr_chat_image"`, khong co `document_id`.
- Gia su image size limit: 10MB, format: jpg/png/webp.

---

## OpenSearch index schema contract

Index name: `iccp_documents_{organization_id}` (mot index per org).
URL: `http://opensearch:9200/iccp_documents_{org_id}` (internal Docker network).
Bien moi truong: `OPENSEARCH_URL`, `OPENSEARCH_INDEX_PREFIX`.

```json
{
  "mappings": {
    "properties": {
      "document_id":     { "type": "keyword" },
      "chunk_id":        { "type": "keyword" },
      "chunk_index":     { "type": "integer" },
      "organization_id": { "type": "keyword" },
      "content":         { "type": "text", "analyzer": "standard" },
      "title":           { "type": "text", "analyzer": "standard" },
      "file_name":       { "type": "keyword" },
      "file_type":       { "type": "keyword" },
      "access_scope":    { "type": "keyword" },
      "project_id":      { "type": "keyword" },
      "allowed_project_ids": { "type": "keyword" },
      "allowed_role_ids":    { "type": "keyword" },
      "allowed_user_ids":    { "type": "keyword" },
      "source_type":     { "type": "keyword" },
      "ocr_confidence":  { "type": "float" },
      "token_count":     { "type": "integer" },
      "indexed_at":      { "type": "date" }
    }
  }
}
```

---

## Document lifecycle — Van de va giai phap

### Cac kich ban nguy hiem (chunks bi stale trong Pinecone/OpenSearch)

#### Kich ban 1: Soft delete (isActive = false)
```
be_core: doc.isActive = false  →  vao recovery
Pinecone/OpenSearch: CHUNKS VAN CON NGUYEN
→ User van chat duoc, van lay duoc chunks cua doc "da xoa"

Fix:
- batch-access-check PHAI kiem tra isActive = true
- Neu isActive = false → tra ve document_id trong danh sach "denied"
- Lam nay dam bao ca revoke quyen lan soft delete deu block o 1 cho
```

#### Kich ban 2: Hard delete (permanent, isActive = false truoc)
```
be_core: xoa doc khoi DB + xoa file ImageKit
Pinecone/OpenSearch: CHUNKS VAN CON (be_core khong tu goi be_ai)
be_ai co DELETE /api/v1/ingest/documents/{id} nhung CHUA BAO GIO duoc goi tu dong

Fix:
- be_core.deletePermanent() PHAI goi be_ai DELETE vector
- Goi qua HttpService trong DocumentCoreService.deletePermanent()
- Neu be_ai fail (timeout, error) → log warning, khong block hard delete
- Hard delete chu y: vi doc da bi soft-delete truoc (isActive=false),
  batch-access-check da block roi nen day la cleanup cuoi cung, khong urgent
```

#### Kich ban 3: Upload version moi (thay the noi dung)
```
be_core: uploadNewVersion() → status=PENDING, filePath → file moi, version++
Pinecone/OpenSearch: CHUNKS CU VAN TON TAI (tu version truoc)

Neu version moi co nhieu chunk hon version cu:
  → Pinecone overwrite dung cac chunk cu (ID: {doc_id}_{chunk_index})
  → Chunk index cao hon duoc them vao

Neu version moi co it chunk hon version cu:
  → chunk index cao co tu version cu van con trong Pinecone
  → Vi du: v1 co 10 chunks (index 0-9), v2 chi co 7 chunks (index 0-6)
  → Pinecone con lai chunks 7, 8, 9 cua v1 lan lon voi v2

Fix:
- IngestionAgent PHAI XOA TOAN BO chunks cu truoc khi index version moi
- Xoa bang Pinecone delete by filter: {document_id: doc_id}
- Sau do index lai toan bo chunks tu version moi
- OpenSearch: delete by query {document_id: doc_id}, sau do bulk index moi
```

#### Kich ban 4: Update metadata (doi accessScope, folderId)
```
be_core: update accessScope hoac folderId trong DB
Pinecone/OpenSearch: metadata access_scope, project_id VAN LA GIA TRI CU

Vi du: accessScope doi tu "project" → "organization"
  Pinecone pre-filter van block doc nay neu user khong trong project_ids
  Nhung runtime ACL check be_core tra ve "allowed" (vi hien tai la organization)
  → Ket qua: pre-filter sai nhung runtime check dung → user khong thay doc

Fix:
- Sau khi update accessScope/folderId trong be_core, trigger sync metadata be_ai
- be_ai: them PATCH /internal/documents/{id}/metadata endpoint
- Cap nhat metadata trong Pinecone (upsert voi metadata moi, giu vector cu)
- Cap nhat trong OpenSearch (update by document_id)
- Hoac don gian: force re-index toan bo document (xoa + index lai)
```

---

## ACL contract — Van de va giai phap

### Van de co ban
be_core ACL la **DYNAMIC** (kiem tra live DB moi request).
Pinecone/OpenSearch metadata la **STATIC SNAPSHOT** tai thoi diem ingestion.
Khi be_core revoke quyen, Pinecone KHONG tu dong cap nhat.

**Hau qua hien tai:** RetrievalAgent chi filter theo `organization_id` — bat ky user
nao trong cung org deu lay duoc chunks, ke ca da bi revoke quyen trong be_core.

### Giai phap: Post-retrieval runtime ACL check

```
Pinecone/OpenSearch tra ve top-K chunks (filter so bo theo org_id + access_scope)
         ↓
POST /internal/documents/batch-access-check (be_core)
  Body: { user_id, organization_id, document_ids: ["id1", "id2", ...] }
  Response: { allowed: ["id1"], denied: ["id2"] }
         ↓
be_ai filter bo chunks co document_id trong danh sach "denied"
         ↓
Chi cac chunks da qua ACL moi duoc dua vao LLM context
```

Endpoint nay phai dung InternalKeyGuard va query LIVE database be_core —
dam bao neu be_core revoke quyen thi ngay lap tuc user khong lay duoc chunks.

### Metadata trong Pinecone/OpenSearch (de so loc so bo)
Moi chunk khi index PHAI co:

| Field | Type | Muc dich |
|---|---|---|
| `organization_id` | string | Hard isolation — filter truoc tien |
| `access_scope` | enum | `organization \| project \| role \| user` — so loc so bo |
| `project_id` | string? | Filter nhanh cho project-scoped docs |
| `document_id` | string | Dung cho runtime ACL check voi be_core |

> **Luu y:** KHONG luu `allowed_user_ids` / `allowed_role_ids` vao Pinecone metadata
> vi chung thay doi dong khi be_core cap nhat quyen. Chi dung de so loc so bo,
> kiem tra chinh xac phai qua be_core runtime check.

### Pre-filter so bo trong Pinecone (truoc khi goi be_core)
```python
# Truoc: chi filter org_id (sai)
{ "organization_id": org_id }

# Sau: filter co biet access_scope de giam tap candidate
{
  "organization_id": org_id,
  "$or": [
    {"access_scope": "organization"},           # Mo cho tat ca org members
    {"access_scope": "system"},                 # Mo cho tat ca org members
    {"project_id": {"$in": user_project_ids}},  # Project-scoped, neu user la member
  ]
}
# Sau do LOC LAI bang runtime ACL check voi be_core
```

### Nguon du lieu user cho filter:
- `user_id`: tu JWT (da co trong CurrentUser)
- `role_ids`: goi `GET /v1/rbac/my-roles` hoac lay tu JWT claims (hien tai chua truyen)
- `project_ids`: goi `GET /v1/projects/my-projects` hoac lay tu JWT claims

---

## Citation contract bat buoc

Moi phan tu `citations[]` toi thieu co:

| Field | Co khi nao |
|---|---|
| `document_id` | Luon co (tru OCR chat image) |
| `document_title` | Luon co |
| `chunk_id` | Luon co |
| `chunk_preview` | Luon co |
| `source_type` | Luon co: `text \| ocr_document \| ocr_chat_image` |
| `score_lexical` | Khi co OpenSearch retrieval |
| `score_vector` | Khi co Pinecone retrieval |
| `score_graph` | Khi co graph rerank |
| `final_score` | Luon co (RRF score hoac fallback = score_vector) |
| `ocr_confidence` | Khi `source_type` la OCR |

---

## Ke hoach trien khai (POC Slots)

### Slot 1: Fix blocking va contracts (hien tai)

**Internal routes be_core → be_ai:**
- [ ] be_core: them `GET /internal/documents/{id}` (thong tin doc + file path).
- [ ] be_core: them `PATCH /internal/documents/{id}/status` (cap nhat status tu be_ai).
- [ ] be_core: them `POST /internal/documents/batch-access-check` (kiem tra quyen + isActive nhieu doc 1 lan).

**Document lifecycle (stale chunks):**
- [ ] be_core: `deletePermanent()` phai goi `DELETE /api/v1/ingest/documents/{id}` trong be_ai.
- [ ] be_core: `update()` khi doi `accessScope`/`folderId` phai goi `PATCH /internal/documents/{id}/metadata` trong be_ai.
- [ ] be_ai: `IngestionAgent` phai xoa chunks cu truoc khi index (delete_by_filter Pinecone + delete_by_query OpenSearch).

**ACL trong retrieval:**
- [ ] be_ai: messages.py truyen `role_ids` va `project_ids` vao `OrchestratorInput`.
- [ ] be_ai: RetrievalAgent goi `batch-access-check` sau khi lay chunks (post-retrieval filter).

**Web:**
- [ ] Wire "Trigger chunking" button → `POST /api/v1/ingest/documents`.
- [ ] Real-time status update qua WebSocket khi document doi sang `indexed`.

### Slot 2: Ingestion + OCR document
- [ ] Parse tai lieu (da co FileParserService).
- [ ] OCR voi file scan/image-based (Tesseract).
- [ ] Chunk va gan metadata ACL day du.
- [ ] Save chunks vao be_core (POST /internal/documents/{id}/chunks).

### Slot 3: Dual indexing (OpenSearch + Pinecone)
- [ ] Tao OpenSearchService.
- [ ] IngestionAgent upsert ca OpenSearch va Pinecone.
- [ ] Kiem tra recall co ban.

### Slot 4: Document-scoped chat
- [ ] be_ai RetrievalAgent filter theo `context_scope` = `document` hoac `folder`.
- [ ] Web UI: nut "Chat ve tai lieu nay" tren document detail page.
- [ ] Web UI: scope selector trong chatbot sidebar.
- [ ] be_core WebSocket event khi document doi status (INDEXED).

### Slot 5: Graph linking + rerank
- [ ] Extract entity/relation co ban tu chunk.
- [ ] be_core: them graph-table (entities, relations, chunk_entity_links).
- [ ] Tinh `score_graph` de rerank sau RRF.

### Slot 6: OCR chat image
- [ ] API nhan multipart/form-data hoac base64 image trong message.
- [ ] OCRService.extract_text(image).
- [ ] Append image_context vao ChatInput.

### Slot 7: E2E va bugfix
- [ ] Test khac tenant.
- [ ] Test cung tenant khac role / project.
- [ ] Test OCR document + OCR chat image.
- [ ] Benchmark recall@10.

---

## Acceptance benchmark
- Recall@10 (20 cau hoi noi bo): hybrid >= vector-only.
- Ty le citation hop le >= 90%.
- Khong ro ri cross-tenant trong test thu cong.
- Citation map duoc ve `kb-index.md`.

## Risk va giam thieu
- OCR noise cao: ap dung preprocess + confidence threshold (< 0.5 flag, khong block).
- Lech index giua OpenSearch/Pinecone: them trang thai indexing va retry.
- Graph score gay do tre: gioi han candidate truoc rerank (max 50 candidates).
- Image OCR sai: chi dung lam context bo sung, khong lam citation chinh.

## Decision log
- 2026-04-16: Chot stack OpenSearch + Pinecone + Postgre graph-table + Tesseract OCR.
- 2026-04-16: Giu MongoDB hien huu cho conversation/messages/quota/ingest jobs.
- 2026-04-17: Phan tach ingest_status be_core (DocumentStatus enum) vs KB tracking status. Hai he thong doc lap.
- 2026-04-17: OCR chat image chi la context bo sung, khong index vao KB.
- 2026-04-17: role_ids trong OrchestratorInput phai duoc lay tu JWT truoc khi goi retrieval.
