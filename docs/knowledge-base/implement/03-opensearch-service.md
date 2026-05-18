# 03 — be_ai: OpenSearchService

> **Repo:** `iccp_be_ai`
> **Prerequisite:** OpenSearch container đang chạy (`docker-compose up opensearch`)
> **File tạo mới:** `app/services/opensearch_service.py`
> **Được dùng bởi:** file 02 (ingestion), file 04 (retrieval)

---

## Mục tiêu

Tạo `OpenSearchService` cung cấp:
- `bulk_index` — index chunks vào lexical store
- `search` — BM25 full-text search với ACL filter
- `delete_by_document_id` — xóa tất cả chunks của 1 document
- `ensure_index` — tạo index với mapping nếu chưa có

Index naming convention: `{OPENSEARCH_INDEX_PREFIX}_{organization_id}`
(VD: `iccp_documents_abc-org-uuid`)

---

## Bước 1 — Cài dependency

```bash
pip install opensearch-py
```

Thêm vào `requirements.txt`:
```
opensearch-py==2.7.x
```

---

## Bước 2 — Thêm config vào `app/core/config.py`

```python
class Settings(BaseSettings):
    # ... existing fields ...

    # OpenSearch
    OPENSEARCH_URL: str = "http://opensearch:9200"
    OPENSEARCH_INDEX_PREFIX: str = "iccp_documents"
    ENABLE_OPENSEARCH: bool = True
```

---

## Bước 3 — `OpenSearchService`

**File:** `app/services/opensearch_service.py`

```python
"""
OpenSearchService
=================
Lexical search service dùng BM25 (OpenSearch 2.x).
- Một index per organization: {prefix}_{org_id}
- Security disabled ở dev (DISABLE_SECURITY_PLUGIN=true)
- Tất cả method đều async (dùng asyncio.to_thread cho SDK sync)
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Optional

import structlog
from opensearchpy import OpenSearch, RequestError

from app.core.config import settings
from app.services.chunking_service import Chunk

log = structlog.get_logger(__name__)

# ── Index mapping (chỉ tạo 1 lần khi ensure_index) ─────────────────────────
_INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,  # dev: 0, prod: 1
        "analysis": {
            "analyzer": {
                "vi_standard": {
                    "type": "standard",
                    "stopwords": "_none_",
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "document_id":       {"type": "keyword"},
            "chunk_index":       {"type": "integer"},
            "organization_id":   {"type": "keyword"},
            "content":           {"type": "text", "analyzer": "vi_standard"},
            "file_name":         {"type": "keyword"},
            "file_type":         {"type": "keyword"},
            "access_scope":      {"type": "keyword"},
            "project_id":        {"type": "keyword"},
            "folder_id":         {"type": "keyword"},
            "source_type":       {"type": "keyword"},
            "ocr_confidence":    {"type": "float"},
            "token_count":       {"type": "integer"},
            "indexed_at":        {"type": "date"},
        }
    },
}


@dataclass
class OpenSearchHit:
    document_id: str
    chunk_index: int
    content: str
    score: float
    file_name: str
    file_type: str
    access_scope: str
    project_id: Optional[str]
    source_type: str
    metadata: dict[str, Any]


class OpenSearchService:
    """
    Stateless service — khởi tạo client một lần trong lifespan (app/main.py).

    Rule:
    - Luôn dùng index name = f"{prefix}_{org_id}" (tenant isolation).
    - Mọi operation đều gọi asyncio.to_thread() vì SDK là synchronous.
    - Nếu ENABLE_OPENSEARCH=False, mọi method trả về ngay (no-op).
    """

    _client: Optional[OpenSearch] = None

    @classmethod
    def get_client(cls) -> OpenSearch:
        if cls._client is None:
            cls._client = OpenSearch(
                hosts=[settings.OPENSEARCH_URL],
                use_ssl=False,
                verify_certs=False,
                http_compress=True,
                timeout=10,
            )
        return cls._client

    @classmethod
    def index_name(cls, organization_id: str) -> str:
        return f"{settings.OPENSEARCH_INDEX_PREFIX}_{organization_id}"

    # ── ensure_index ────────────────────────────────────────────────────────
    @classmethod
    async def ensure_index(cls, organization_id: str) -> None:
        """Tạo index nếu chưa tồn tại. Gọi trong ingestion trước khi bulk_index."""
        if not settings.ENABLE_OPENSEARCH:
            return

        name = cls.index_name(organization_id)
        client = cls.get_client()

        def _create():
            if not client.indices.exists(index=name):
                client.indices.create(index=name, body=_INDEX_MAPPING)
                log.info("opensearch.index_created", index=name)

        await asyncio.to_thread(_create)

    # ── bulk_index ───────────────────────────────────────────────────────────
    @classmethod
    async def bulk_index(
        cls,
        organization_id: str,
        document_id: str,
        chunks: list[Chunk],
    ) -> None:
        """
        Index toàn bộ chunks của 1 document vào OpenSearch.
        Document ID trong OpenSearch: "{document_id}_{chunk_index}"
        """
        if not settings.ENABLE_OPENSEARCH or not chunks:
            return

        await cls.ensure_index(organization_id)

        name = cls.index_name(organization_id)
        client = cls.get_client()

        # Build bulk body: action + doc xen kẽ nhau
        bulk_body = []
        for chunk in chunks:
            chunk_id = f"{document_id}_{chunk.chunk_index}"
            bulk_body.append({"index": {"_index": name, "_id": chunk_id}})
            bulk_body.append({
                "document_id":     document_id,
                "chunk_index":     chunk.chunk_index,
                "organization_id": organization_id,
                "content":         chunk.content,
                "file_name":       chunk.metadata.get("file_name", ""),
                "file_type":       chunk.metadata.get("file_type", ""),
                "access_scope":    chunk.metadata.get("access_scope", "organization"),
                "project_id":      chunk.metadata.get("project_id") or None,
                "folder_id":       chunk.metadata.get("folder_id") or None,
                "source_type":     chunk.metadata.get("source_type", "text"),
                "ocr_confidence":  chunk.metadata.get("ocr_confidence", 1.0),
                "token_count":     chunk.token_count,
                "indexed_at":      "now",
            })

        def _bulk():
            resp = client.bulk(body=bulk_body, timeout="30s")
            if resp.get("errors"):
                failed = [
                    item for item in resp["items"]
                    if item.get("index", {}).get("status", 200) >= 400
                ]
                log.warning("opensearch.bulk_partial_fail", failed_count=len(failed))

        await asyncio.to_thread(_bulk)
        log.info("opensearch.bulk_indexed", document_id=document_id, chunks=len(chunks))

    # ── search ───────────────────────────────────────────────────────────────
    @classmethod
    async def search(
        cls,
        organization_id: str,
        query: str,
        top_k: int = 6,
        context_scope: str = "organization",
        context_id: Optional[str] = None,
        project_ids: Optional[list[str]] = None,
    ) -> list[OpenSearchHit]:
        """
        BM25 search với pre-filter ACL (scope + project).
        Chỉ là pre-filter — vẫn cần runtime ACL check với be_core sau.
        """
        if not settings.ENABLE_OPENSEARCH:
            return []

        name = cls.index_name(organization_id)
        client = cls.get_client()

        # Build filter dựa trên scope
        access_filter = cls._build_access_filter(
            context_scope, context_id, project_ids
        )

        query_body = {
            "query": {
                "bool": {
                    "must": [
                        {"match": {"content": {"query": query, "boost": 1.0}}}
                    ],
                    "filter": access_filter,
                }
            },
            "size": top_k,
            "_source": True,
        }

        def _search():
            return client.search(index=name, body=query_body)

        try:
            resp = await asyncio.to_thread(_search)
        except Exception as exc:
            log.error("opensearch.search_failed", error=str(exc))
            return []

        hits = []
        for hit in resp.get("hits", {}).get("hits", []):
            src = hit["_source"]
            hits.append(OpenSearchHit(
                document_id=src["document_id"],
                chunk_index=src["chunk_index"],
                content=src["content"],
                score=hit["_score"],
                file_name=src.get("file_name", ""),
                file_type=src.get("file_type", ""),
                access_scope=src.get("access_scope", "organization"),
                project_id=src.get("project_id"),
                source_type=src.get("source_type", "text"),
                metadata=src,
            ))

        return hits

    # ── delete_by_document_id ────────────────────────────────────────────────
    @classmethod
    async def delete_by_document_id(
        cls,
        organization_id: str,
        document_id: str,
    ) -> None:
        """Xóa toàn bộ chunks của document_id trong index của org."""
        if not settings.ENABLE_OPENSEARCH:
            return

        name = cls.index_name(organization_id)
        client = cls.get_client()

        def _delete():
            client.delete_by_query(
                index=name,
                body={"query": {"term": {"document_id": document_id}}},
                conflicts="proceed",
            )

        try:
            await asyncio.to_thread(_delete)
            log.info("opensearch.deleted_by_doc", document_id=document_id)
        except Exception as exc:
            # Không fail ingestion nếu index chưa tồn tại
            log.warning("opensearch.delete_failed", document_id=document_id, error=str(exc))

    # ── delete_index ─────────────────────────────────────────────────────────
    @classmethod
    async def delete_index(cls, organization_id: str) -> None:
        """Xóa toàn bộ index khi org bị xóa. Dùng thận trọng."""
        if not settings.ENABLE_OPENSEARCH:
            return

        name = cls.index_name(organization_id)
        client = cls.get_client()

        await asyncio.to_thread(
            lambda: client.indices.delete(index=name, ignore_unavailable=True)
        )
        log.info("opensearch.index_deleted", organization_id=organization_id)

    # ── Private helpers ───────────────────────────────────────────────────────
    @classmethod
    def _build_access_filter(
        cls,
        context_scope: str,
        context_id: Optional[str],
        project_ids: Optional[list[str]],
    ) -> list[dict]:
        """
        Pre-filter scope. Kết quả vẫn cần runtime ACL check với be_core.
        Chỉ là tối ưu để giảm số lượng candidates.
        """
        filters = []

        if context_scope == "document" and context_id:
            # Chat về 1 doc cụ thể
            filters.append({"term": {"document_id": context_id}})

        elif context_scope == "project" and context_id:
            # Chat trong context project
            filters.append({
                "bool": {
                    "should": [
                        {"terms": {"access_scope": ["organization", "system"]}},
                        {"term": {"project_id": context_id}},
                    ],
                    "minimum_should_match": 1,
                }
            })

        else:
            # Organization scope (default) — chỉ lấy org + system scoped
            filters.append({
                "terms": {"access_scope": ["organization", "system"]}
            })

        return filters
```

---

## Bước 4 — Khởi tạo trong `main.py`

```python
# app/main.py — trong lifespan()
from app.services.opensearch_service import OpenSearchService

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... init khác ...
    if settings.ENABLE_OPENSEARCH:
        OpenSearchService.get_client()  # Khởi tạo connection pool
        log.info("opensearch.connected", url=settings.OPENSEARCH_URL)
    yield
    # cleanup nếu cần
```

---

## Test thủ công

```bash
# Kiểm tra OpenSearch đang chạy
curl http://localhost:9200/_cluster/health

# Xem các index đang có
curl http://localhost:9200/_cat/indices?v

# Xem mapping của index
curl http://localhost:9200/iccp_documents_{org_id}/_mapping

# Search thủ công
curl -X POST http://localhost:9200/iccp_documents_{org_id}/_search \
  -H "Content-Type: application/json" \
  -d '{"query": {"match": {"content": "quy trình onboarding"}}}'
```

---

## Checklist file này

- [ ] `opensearch-py` thêm vào `requirements.txt`
- [ ] `OPENSEARCH_URL` và `ENABLE_OPENSEARCH` có trong `app/core/config.py`
- [ ] `ENABLE_OPENSEARCH=False` thì tất cả method no-op (không crash)
- [ ] `ensure_index` tạo mapping trước khi bulk_index
- [ ] `delete_by_document_id` không crash nếu index chưa tồn tại (`log.warning` thay `raise`)
- [ ] `search` trả về `[]` nếu OpenSearch lỗi (không block chat)
- [ ] Index name = `{prefix}_{org_id}` — không dùng global index chung
