# Implement Guide — Mục lục

> Bộ tài liệu này hướng dẫn implement toàn bộ hệ thống **Hybrid RAG Chatbot** cho ICCP.
> Đọc theo thứ tự số. Mỗi file phụ thuộc vào file trước.

---

## Thứ tự đọc & implement

> **Chú ý trạng thái:** "Code done" = code đã viết. "Blueprint" = chỉ là hướng dẫn, chưa có code.

| # | File | Repo | Nội dung | Trạng thái |
|---|---|---|---|---|
| 01 | [01-be-core-internal-routes.md](./01-be-core-internal-routes.md) | `iccp_be_core` | Internal API endpoints cho be_ai gọi vào | 🔴 Blueprint |
| 02 | [02-ingestion-pipeline.md](./02-ingestion-pipeline.md) | `iccp_be_ai` | Pipeline ingestion: parse → OCR → chunk → dual index | 🔴 Blueprint (OCR + parser đã code) |
| 03 | [03-opensearch-service.md](./03-opensearch-service.md) | `iccp_be_ai` | OpenSearchService: index, search, delete | 🔴 Blueprint |
| 04 | [04-acl-retrieval.md](./04-acl-retrieval.md) | `iccp_be_ai` | Runtime ACL check: RRF + batch-access-check | 🔴 Blueprint |
| 05 | [05-document-lifecycle.md](./05-document-lifecycle.md) | `iccp_be_core` + `iccp_be_ai` | Hard delete → delete vectors, metadata sync | 🔴 Blueprint |
| 06 | [06-web-integration.md](./06-web-integration.md) | `iccp_web` | Trigger chunking button + document-scoped chat | 🔴 Blueprint |
| 07 | [07-ocr-image-chat.md](./07-ocr-image-chat.md) | `iccp_be_ai` + `iccp_web` | OCR scanned PDF (Tesseract) + image chat (Gemini Vision) | 🟡 Schema + parser done, endpoint chưa wire |

---

## Dependency graph

```
01-be-core-internal-routes
        │
        ├──► 02-ingestion-pipeline   (cần GET /internal/documents/{id})
        │           │
        │           └──► 03-opensearch-service  (dùng trong ingestion)
        │
        ├──► 04-acl-retrieval        (cần POST /internal/documents/batch-access-check)
        │
        └──► 05-document-lifecycle   (cần DELETE + PATCH /internal routes)
                    │
                    └──► 06-web-integration  (cần tất cả trên)
                    │
                    └──► 07-ocr-image-chat  (OCR + image chat, độc lập với 06)
```

---

## Conventions bắt buộc (từ `docs/rule.md`)

| Rule | Mô tả |
|---|---|
| **Async first** | Mọi I/O đều `async/await`. Không block event loop. |
| **Tenant isolation** | Mọi Pinecone query và DB query PHẢI có `organization_id`. |
| **No direct DB write** | `be_ai` không write trực tiếp vào be_core tables. Chỉ qua internal HTTP API. |
| **Agent interface** | Agent extend `BaseAgent`, implement `run(input) → output`. |
| **Typed I/O** | Agent nhận `@dataclass` input, trả `@dataclass` output. Không dùng raw dict. |
| **Prompt tách riêng** | Tất cả prompt trong `app/prompts/{name}.py`. Không hardcode trong agent. |
| **BeCoreClient** | Mọi call tới be_core đi qua `app/clients/be_core_client.py`. |
| **structlog** | Log phải có `trace_id`, `organization_id`. Không log secrets/PII. |

---

## Files mới cần tạo

### `iccp_be_ai`
```
app/
├── services/
│   ├── opensearch_service.py          ← MỚI (file 03)
│   ├── file_parser_service.py         ← SỬA (file 07: Excel + Tesseract OCR)
│   └── gemini_vision_service.py       ← MỚI (file 07: image chat)
├── agents/
│   ├── ingestion_agent.py             ← SỬA (file 02)
│   └── retrieval_agent.py             ← SỬA (file 04)
├── agents/orchestrator/
│   └── orchestrator.py                ← SỬA (file 04, 07)
├── api/v1/
│   ├── messages.py                    ← SỬA (file 04, 07)
│   └── chat_upload.py                 ← MỚI (file 07: upload ảnh endpoint)
├── db/schemas/
│   └── message.py                     ← SỬA (file 07: thêm image field)
└── clients/
    └── be_core_client.py              ← SỬA (file 01, 05)
```

### `iccp_be_core`
```
src/modules/document/
├── core/
│   ├── core.service.ts                ← SỬA (file 01, 05)
│   └── dto/
│       └── request/
│           └── batch-access-check.request.ts  ← MỚI (file 01)
└── internal/
    ├── internal.controller.ts         ← MỚI (file 01)
    ├── internal.module.ts             ← MỚI (file 01)
    └── internal.service.ts            ← MỚI (file 01)
```

### `iccp_web`
```
src/features/tenant/documents/
├── pages/
│   └── document-detail-page.tsx       ← SỬA (file 06)
└── hooks/
    └── use-document-detail.ts         ← SỬA (file 06)

src/features/common/chatbot/
├── components/
│   └── chatbot-scope-selector.tsx     ← MỚI (file 06)
└── hooks/
    └── use-chatbot-page.ts            ← SỬA (file 06)
```

---

## Checklist tổng trước khi merge

- [ ] `POST /internal/documents/batch-access-check` hoạt động, trả đúng allowed/denied
- [ ] `IngestionAgent` xóa chunks cũ trước khi index mới
- [ ] `OpenSearchService` upsert + search + delete hoạt động
- [ ] `RetrievalAgent` gọi batch-access-check và filter chunks
- [ ] `messages.py` truyền `role_ids` + `project_ids` vào `OrchestratorInput`
- [ ] `deletePermanent` trong be_core gọi be_ai xóa vectors
- [ ] `update` với `accessScope`/`folderId` gọi be_ai sync metadata
- [ ] Nút "Trigger chunking" gọi đúng API
- [ ] Chatbot hỗ trợ `context_scope=document` và `context_scope=folder`
- [ ] PDF scan → tự động OCR bằng Tesseract → chunks không rỗng
- [ ] `.xlsx` upload → chunks lấy được data từng sheet
- [ ] `POST /v1/chat/upload-image` → trả URL ImageKit
- [ ] `SendMessageRequest` với `image` field → Gemini Vision xử lý → response có context từ ảnh
- [ ] Message history giữ `image.url` → FE hiển thị thumbnail trong chat bubble
- [ ] `tesseract --list-langs` trong container có `vie` + `eng`
