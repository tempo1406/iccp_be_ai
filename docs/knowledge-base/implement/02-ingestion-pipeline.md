# 02 — be_ai: Ingestion Pipeline

> **Repo:** `iccp_be_ai`
> **Prerequisite:** File 01 (cần `GET /internal/documents/:id` và `PATCH .../status`)
> **File chỉnh sửa:** `app/agents/ingestion_agent.py`, `app/workers/tasks/ingest_tasks.py`

---

## Mục tiêu

Cập nhật `IngestionAgent` để:
1. **Xóa chunks cũ trước** khi index (fix stale chunks khi upload version mới)
2. **Dual index** vào cả Pinecone và OpenSearch song song
3. **Content policy gate** chặn toàn bộ nếu vi phạm (đã có, đảm bảo đúng flow)
4. **Lưu chunks về be_core** sau khi index thành công

---

## Flow đầy đủ

```
ingest_document_task (Celery)
    │
    ├─ 1. Mark job STARTED
    ├─ 2. GET /internal/documents/{id}  →  lấy filePath, mimeType, accessScope
    ├─ 3. Download file từ ImageKit URL
    │
    ├─ 4. FileParserService.parse()     →  raw_text
    │         └─ nếu scan/image PDF    →  OCRService.extract()  →  raw_text
    │
    ├─ 5. ContentPolicyService.check_document()
    │         ├─ PASS  →  tiếp tục
    │         └─ FAIL  →  status="policy_rejected", STOP
    │
    ├─ 6. ChunkingService.chunk()       →  List[Chunk] với metadata ACL
    │
    ├─ 7. EmbeddingService.embed_batch() →  List[vector]
    │
    ├─ 8. XÓA CHUNKS CŨ (quan trọng — trước khi upsert mới):
    │         ├─ PineconeService.delete_by_filter({document_id: id})
    │         └─ OpenSearchService.delete_by_document_id(org_id, document_id)
    │
    ├─ 9. DUAL INDEX song song:
    │         ├─ PineconeService.upsert(vectors, namespace=org_{org_id})
    │         └─ OpenSearchService.bulk_index(org_id, chunks)
    │
    ├─ 10. BeCoreClient.update_status("indexed")
    ├─ 11. BeCoreClient.save_chunks(document_id, chunks)
    └─ 12. Mark job SUCCESS
```

---

## Bước 1 — Cập nhật `IngestionAgent`

**File:** `app/agents/ingestion_agent.py`

```python
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.clients.be_core_client import BeCoreClient
from app.core.config import settings
from app.services.chunking_service import ChunkingService, Chunk
from app.services.content_policy_service import ContentPolicyService
from app.services.embedding_service import EmbeddingService
from app.services.file_parser_service import FileParserService
from app.services.opensearch_service import OpenSearchService
from app.services.pinecone_service import PineconeService, PineconeVector

log = structlog.get_logger(__name__)


@dataclass
class IngestionInput(AgentInput):
    document_id: str = ""
    file_path: str = ""          # ImageKit URL
    file_name: str = ""
    file_type: str = ""
    mime_type: str = ""
    access_scope: str = "organization"
    project_id: Optional[str] = None
    folder_id: Optional[str] = None
    allowed_role_ids: list[str] = field(default_factory=list)
    allowed_project_ids: list[str] = field(default_factory=list)
    allowed_user_ids: list[str] = field(default_factory=list)
    is_reindex: bool = False     # True nếu là version mới — cần xóa chunks cũ


@dataclass
class IngestionOutput(AgentOutput):
    document_id: str = ""
    chunks_count: int = 0
    policy_rejected: bool = False
    rejection_reason: str = ""


class IngestionAgent(BaseAgent):
    """
    Pipeline: parse → policy check → chunk → embed → [xóa cũ] → dual index.

    Rule:
    - LUÔN xóa chunks cũ trước khi upsert (is_reindex=True hoặc để an toàn luôn làm).
    - Pinecone và OpenSearch được index SONG SONG bằng asyncio.gather().
    - Nếu xóa cũ thành công nhưng index mới fail → status=failed, cần retry.
    - ContentPolicyService fail → KHÔNG index gì cả, status=policy_rejected.
    """

    async def run(self, input: AgentInput) -> IngestionOutput:
        assert isinstance(input, IngestionInput)
        start_ms = time.monotonic()

        structlog.contextvars.bind_contextvars(
            document_id=input.document_id,
            organization_id=input.organization_id,
            trace_id=input.trace_id,
        )

        # ── Step 1: Download file ──────────────────────────────────────
        raw_bytes = await self._download_file(input.file_path)

        # ── Step 2: Parse ─────────────────────────────────────────────
        raw_text = await FileParserService.parse_bytes(
            data=raw_bytes,
            file_name=input.file_name,
            mime_type=input.mime_type,
        )

        # ── Step 3: Content policy check (gate) ───────────────────────
        try:
            await ContentPolicyService.check_document(
                text=raw_text,
                document_id=input.document_id,
                organization_id=input.organization_id,
                file_name=input.file_name,
            )
        except Exception as exc:
            log.warning("ingestion.policy_rejected", error=str(exc))
            return IngestionOutput(
                success=False,
                document_id=input.document_id,
                policy_rejected=True,
                rejection_reason=str(exc),
            )

        # ── Step 4: Chunk ─────────────────────────────────────────────
        acl_metadata = {
            "organization_id": input.organization_id,
            "document_id": input.document_id,
            "file_name": input.file_name,
            "file_type": input.file_type,
            "access_scope": input.access_scope,
            "project_id": input.project_id,
            "folder_id": input.folder_id,
        }
        chunks: list[Chunk] = ChunkingService.chunk_text(raw_text, metadata=acl_metadata)

        # ── Step 5: Embed ─────────────────────────────────────────────
        texts = [c.content for c in chunks]
        vectors = await EmbeddingService.embed_batch(texts)

        # ── Step 6: Xóa chunks cũ TRƯỚC (tránh stale data) ───────────
        namespace = f"org_{input.organization_id}"
        await self._delete_old_chunks(
            namespace=namespace,
            organization_id=input.organization_id,
            document_id=input.document_id,
        )

        # ── Step 7: Dual index song song ─────────────────────────────
        pinecone_vectors = self._build_pinecone_vectors(input, chunks, vectors)
        await asyncio.gather(
            PineconeService.upsert(vectors=pinecone_vectors, namespace=namespace),
            OpenSearchService.bulk_index(
                organization_id=input.organization_id,
                document_id=input.document_id,
                chunks=chunks,
            ),
        )

        elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        log.info(
            "ingestion.complete",
            document_id=input.document_id,
            chunks=len(chunks),
            elapsed_ms=elapsed_ms,
        )

        return IngestionOutput(
            success=True,
            document_id=input.document_id,
            chunks_count=len(chunks),
        )

    async def _download_file(self, url: str) -> bytes:
        """Download file từ ImageKit URL."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content

    async def _delete_old_chunks(
        self,
        namespace: str,
        organization_id: str,
        document_id: str,
    ) -> None:
        """Xóa toàn bộ chunks cũ trong Pinecone và OpenSearch cho document này."""
        await asyncio.gather(
            PineconeService.delete_by_filter(
                namespace=namespace,
                filter={"document_id": {"$eq": document_id}},
            ),
            OpenSearchService.delete_by_document_id(
                organization_id=organization_id,
                document_id=document_id,
            ),
            return_exceptions=True,  # Không fail nếu chưa có chunks cũ
        )

    def _build_pinecone_vectors(
        self,
        input: IngestionInput,
        chunks: list[Chunk],
        vectors: list[list[float]],
    ) -> list[PineconeVector]:
        indexed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return [
            PineconeVector(
                id=f"{input.document_id}_{i}",
                values=vectors[i],
                metadata={
                    "document_id": input.document_id,
                    "organization_id": input.organization_id,
                    "chunk_index": i,
                    "content": chunk.content,
                    "file_name": input.file_name,
                    "file_type": input.file_type,
                    "access_scope": input.access_scope,
                    "project_id": input.project_id or "",
                    "token_count": chunk.token_count,
                    "source_type": chunk.metadata.get("source_type", "text"),
                    "ocr_confidence": chunk.metadata.get("ocr_confidence", 1.0),
                    "indexed_at": indexed_at,
                },
            )
            for i, chunk in enumerate(chunks)
        ]
```

---

## Bước 2 — Cập nhật `ingest_tasks.py`

**File:** `app/workers/tasks/ingest_tasks.py` — thay thế phần gọi `IngestionAgent`:

```python
@celery_app.task(bind=True, max_retries=3, default_retry_delay=60, queue="ingest")
def ingest_document_task(
    self,
    document_id: str,
    organization_id: str,
    triggered_by: str = "manual",
) -> None:
    """
    Celery task chạy ingestion pipeline.

    Nhận document_id và organization_id.
    Tự lấy file_path từ be_core qua internal API.
    """
    asyncio.run(_async_ingest(self, document_id, organization_id))


async def _async_ingest(task, document_id: str, organization_id: str) -> None:
    be_core = BeCoreClient()
    job_repo = IngestJobRepository()

    # 1. Lấy thông tin document từ be_core
    try:
        doc_info = await be_core.get_document_info(document_id)
    except Exception as exc:
        log.error("ingest.get_doc_info_failed", document_id=document_id, error=str(exc))
        raise task.retry(exc=exc)

    # 2. Update status → processing
    await be_core.update_document_status(document_id, "processing")
    await job_repo.mark_started(document_id)

    # 3. Chạy IngestionAgent
    agent = IngestionAgent()
    result = await agent.run(IngestionInput(
        organization_id=organization_id,
        user_id="system",
        trace_id=f"ingest-{document_id}",
        document_id=document_id,
        file_path=doc_info["filePath"],
        file_name=doc_info["fileName"] or "",
        file_type=doc_info["fileType"] or "",
        mime_type=doc_info["mimeType"] or "",
        access_scope=doc_info["accessScope"],
        project_id=doc_info.get("folderId"),  # lấy từ folder nếu cần
        is_reindex=True,  # luôn xóa chunks cũ để an toàn
    ))

    if result.policy_rejected:
        await be_core.update_document_status(document_id, "failed",
                                              error_message=result.rejection_reason)
        await job_repo.mark_failed(document_id, result.rejection_reason)
        return  # Không retry — đây là lỗi content, không phải technical

    # 4. Lưu chunks về be_core
    # (be_core lưu vào knowledge.document_chunks để dùng cho graph linking sau)
    if result.chunks_count > 0:
        await be_core.save_document_chunks(
            document_id=document_id,
            chunks=agent.last_chunks,  # expose từ agent nếu cần
        )

    # 5. Update status → indexed
    await be_core.update_document_status(
        document_id,
        "indexed",
        indexed_chunks=result.chunks_count,
    )
    await job_repo.mark_success(document_id, result.chunks_count)

    log.info("ingest.task_complete", document_id=document_id, chunks=result.chunks_count)
```

---

## Bước 3 — Expose `POST /api/v1/ingest/documents` (đã có, chỉ verify)

Endpoint này nhận từ Web khi user bấm "Trigger chunking":

```python
# app/api/v1/ingest.py (xác nhận body đúng)
@router.post("/ingest/documents", status_code=202)
async def trigger_ingest(body: IngestDocumentRequest, ...):
    # body: { document_id, organization_id }
    # Tạo IngestJob record trong MongoDB
    # Queue Celery task: ingest_document_task.delay(document_id, organization_id)
    return { "job_id": job.id, "status": "queued" }
```

---

## Chunk metadata schema (đầy đủ)

Mỗi `Chunk` phải có đủ metadata sau để index vào cả Pinecone và OpenSearch:

```python
@dataclass
class Chunk:
    content: str
    token_count: int
    chunk_index: int
    metadata: dict  # Phải có các field sau:
    # {
    #   "organization_id": str,
    #   "document_id": str,
    #   "file_name": str,
    #   "file_type": str,
    #   "access_scope": str,       # organization | project | role | user
    #   "project_id": str | None,
    #   "folder_id": str | None,
    #   "source_type": str,        # text | ocr_document
    #   "ocr_confidence": float,   # 1.0 nếu không phải OCR
    # }
```

---

## Checklist file này

- [ ] `IngestionAgent._delete_old_chunks()` chạy TRƯỚC khi upsert
- [ ] Pinecone và OpenSearch được index SONG SONG bằng `asyncio.gather()`
- [ ] `policy_rejected` không retry (lỗi content, không phải technical)
- [ ] `ingest_document_task` lấy `file_path` từ be_core qua `GET /internal/documents/{id}`
- [ ] Status flow đúng: `pending → processing → indexed | failed`
- [ ] `return_exceptions=True` trong `_delete_old_chunks` để không fail nếu chưa có chunks cũ
