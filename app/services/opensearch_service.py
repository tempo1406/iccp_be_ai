"""
OpenSearchService
=================
Lexical search service dùng BM25 (OpenSearch 2.x).

Conventions:
- Một index per organization: {OPENSEARCH_INDEX_PREFIX}_{org_id}
- Security disabled ở dev (DISABLE_SECURITY_PLUGIN=true trong docker-compose)
- Tất cả public method đều async (dùng asyncio.to_thread vì SDK là synchronous)
- ENABLE_OPENSEARCH=False → mọi method no-op (không crash)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from opensearchpy import OpenSearch, RequestError

from app.core.config import settings
from app.services.chunking_service import Chunk

log = structlog.get_logger(__name__)

# ── Index mapping — tạo 1 lần khi ensure_index ──────────────────────────────
_INDEX_MAPPING = {
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,  # dev: 0  |  prod: 1
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
            "document_id":     {"type": "keyword"},
            "chunk_index":     {"type": "integer"},
            "organization_id": {"type": "keyword"},
            "content":         {"type": "text", "analyzer": "vi_standard"},
            "file_name":       {"type": "keyword"},
            "file_type":       {"type": "keyword"},
            "access_scope":    {"type": "keyword"},
            "owner_user_id":   {"type": "keyword"},
            "project_id":      {"type": "keyword"},
            "folder_id":       {"type": "keyword"},
            "folder_path_ids": {"type": "keyword"},
            "category_id":     {"type": "keyword"},
            "source_type":     {"type": "keyword"},
            "ocr_confidence":  {"type": "float"},
            "token_count":     {"type": "integer"},
            "indexed_at":      {"type": "date"},
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
    Stateless service — khởi tạo client một lần trong main.py lifespan.

    Rules:
    - Index name = f"{prefix}_{org_id}" (tenant isolation).
    - Tất cả operation gọi asyncio.to_thread() vì SDK là synchronous.
    - ENABLE_OPENSEARCH=False → no-op (không crash, không raise).
    - search() trả về [] nếu lỗi (không block chat flow).
    """

    _client: Optional[OpenSearch] = None
    _request_timeout_s: float = 30.0
    _pool_maxsize: int = 10

    @classmethod
    def init_client(cls) -> None:
        """Khởi tạo connection pool. Gọi trong main.py lifespan."""
        cls._client = OpenSearch(
            hosts=[settings.OPENSEARCH_URL],
            use_ssl=False,
            verify_certs=False,
            http_compress=True,
            timeout=10,
            pool_maxsize=cls._pool_maxsize,
        )
        log.info("opensearch.client_initialized", url=settings.OPENSEARCH_URL)

    @classmethod
    def get_client(cls) -> OpenSearch:
        if cls._client is None:
            cls._client = OpenSearch(
                hosts=[settings.OPENSEARCH_URL],
                use_ssl=False,
                verify_certs=False,
                http_compress=True,
                timeout=10,
                pool_maxsize=cls._pool_maxsize,
            )
        return cls._client

    @classmethod
    def index_name(cls, organization_id: str) -> str:
        return f"{settings.OPENSEARCH_INDEX_PREFIX}_{organization_id}"

    # ── ensure_index ─────────────────────────────────────────────────────────

    @classmethod
    async def ensure_index(cls, organization_id: str) -> None:
        """Tạo index nếu chưa tồn tại. Gọi trong ingestion trước khi bulk_index."""
        if not settings.ENABLE_OPENSEARCH:
            return

        name = cls.index_name(organization_id)
        client = cls.get_client()

        def _create() -> None:
            if not client.indices.exists(index=name):
                client.indices.create(index=name, body=_INDEX_MAPPING)
                log.info("opensearch.index_created", index=name)
            else:
                client.indices.put_mapping(
                    index=name,
                    body={"properties": _INDEX_MAPPING["mappings"]["properties"]},
                )

        try:
            await asyncio.to_thread(_create)
        except RequestError as exc:
            # Race condition: index created by another worker between exists() and create()
            if exc.error != "resource_already_exists_exception":
                raise

    # ── bulk_index ────────────────────────────────────────────────────────────

    @classmethod
    async def bulk_index(
        cls,
        organization_id: str,
        document_id: str,
        chunks: list[Chunk],
    ) -> None:
        """
        Index toàn bộ chunks của 1 document vào OpenSearch.
        Document ID trong OpenSearch = "{document_id}_{chunk_index}"
        """
        if not settings.ENABLE_OPENSEARCH or not chunks:
            return

        await cls.ensure_index(organization_id)

        name = cls.index_name(organization_id)
        client = cls.get_client()
        indexed_at = datetime.now(timezone.utc).isoformat()

        bulk_body: list[Any] = []
        for chunk in chunks:
            chunk_id = f"{document_id}_{chunk.chunk_index}"
            bulk_body.append({"index": {"_index": name, "_id": chunk_id}})
            bulk_body.append(
                {
                    "document_id":     document_id,
                    "chunk_index":     chunk.chunk_index,
                    "organization_id": organization_id,
                    "content":         chunk.content,
                    "file_name":       chunk.metadata.get("file_name", ""),
                    "file_type":       chunk.metadata.get("file_type", ""),
                    "access_scope":    chunk.metadata.get("access_scope", "organization"),
                    "owner_user_id":   chunk.metadata.get("owner_user_id") or None,
                    "project_id":      chunk.metadata.get("project_id") or None,
                    "folder_id":       chunk.metadata.get("folder_id") or None,
                    "folder_path_ids": chunk.metadata.get("folder_path_ids") or [],
                    "category_id":     chunk.metadata.get("category_id") or None,
                    "source_type":     chunk.metadata.get("source_type", "text"),
                    "ocr_confidence":  chunk.metadata.get("ocr_confidence", 1.0),
                    "token_count":     chunk.token_count,
                    "indexed_at":      indexed_at,
                }
            )

        def _bulk() -> None:
            # request_timeout must be numeric (seconds) for urllib3 transport
            resp = client.bulk(body=bulk_body, request_timeout=cls._request_timeout_s)
            if resp.get("errors"):
                failed = [
                    item
                    for item in resp["items"]
                    if item.get("index", {}).get("status", 200) >= 400
                ]
                if failed:
                    log.warning(
                        "opensearch.bulk_partial_fail",
                        document_id=document_id,
                        failed_count=len(failed),
                    )

        await asyncio.to_thread(_bulk)
        log.info("opensearch.bulk_indexed", document_id=document_id, chunks=len(chunks))

    # ── search ────────────────────────────────────────────────────────────────

    @classmethod
    async def search(
        cls,
        organization_id: str,
        query: str,
        top_k: int = 6,
        context_scope: str = "organization",
        context_id: Optional[str] = None,
        project_ids: Optional[list[str]] = None,
        user_id: Optional[str] = None,
        context_options: Optional[dict[str, Any]] = None,
    ) -> list[OpenSearchHit]:
        """
        BM25 search với pre-filter ACL (scope + project).

        Chỉ là pre-filter — kết quả vẫn cần runtime ACL check với be_core sau.
        Trả về [] nếu OpenSearch lỗi (không block chat flow).
        """
        if not settings.ENABLE_OPENSEARCH:
            return []

        name = cls.index_name(organization_id)
        client = cls.get_client()

        access_filter = cls._build_access_filter(
            context_scope=context_scope,
            context_id=context_id,
            project_ids=project_ids,
            user_id=user_id,
            context_options=context_options or {},
        )

        query_body: dict[str, Any] = {
            "query": {
                "bool": {
                    "must": [{"match": {"content": {"query": query, "boost": 1.0}}}],
                    "filter": access_filter,
                }
            },
            "size": top_k,
            "_source": True,
        }

        def _search() -> dict:
            return client.search(index=name, body=query_body)

        try:
            resp = await asyncio.to_thread(_search)
        except Exception as exc:
            log.error("opensearch.search_failed", organization_id=organization_id, error=str(exc))
            return []

        hits: list[OpenSearchHit] = []
        for hit in resp.get("hits", {}).get("hits", []):
            src = hit["_source"]
            hits.append(
                OpenSearchHit(
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
                )
            )

        return hits

    # ── delete_by_document_id ─────────────────────────────────────────────────

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

        def _delete() -> None:
            client.delete_by_query(
                index=name,
                body={"query": {"term": {"document_id": document_id}}},
                conflicts="proceed",
            )

        try:
            await asyncio.to_thread(_delete)
            log.info("opensearch.deleted_by_doc", document_id=document_id)
        except Exception as exc:
            # Index chưa tồn tại → bình thường, không fail
            log.warning(
                "opensearch.delete_skipped",
                document_id=document_id,
                reason=str(exc),
            )

    # ── delete_index ──────────────────────────────────────────────────────────

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

    # ── Private helpers ────────────────────────────────────────────────────────

    @classmethod
    def _build_access_filter(
        cls,
        context_scope: str,
        context_id: Optional[str],
        project_ids: Optional[list[str]],
        user_id: Optional[str],
        context_options: dict[str, Any],
    ) -> list[dict]:
        """
        Pre-filter scope.
        Kết quả vẫn cần runtime ACL check với be_core — đây chỉ là tối ưu candidates.
        """
        filters: list[dict] = []
        strict_scope = bool(context_options.get("strict_scope"))
        include_subfolders = bool(context_options.get("include_subfolders"))
        include_shared_docs = bool(context_options.get("include_shared_docs"))
        category = (context_options.get("category") or "").strip()
        file_type = (context_options.get("file_type") or "").strip()
        time_range = (context_options.get("time_range") or "").strip()

        if context_scope == "document" and context_id:
            filters.append({"term": {"document_id": context_id}})

        elif context_scope == "project" and context_id:
            if strict_scope:
                filters.append({"term": {"project_id": context_id}})
            else:
                filters.append(
                    {
                        "bool": {
                            "should": [
                                {"terms": {"access_scope": ["organization", "system"]}},
                                {"term": {"project_id": context_id}},
                            ],
                            "minimum_should_match": 1,
                        }
                    }
                )

        elif context_scope == "folder" and context_id:
            if include_subfolders:
                filters.append({"term": {"folder_path_ids": context_id}})
            else:
                filters.append({"term": {"folder_id": context_id}})

        elif context_scope == "my_docs" and user_id and not include_shared_docs:
            filters.append({"term": {"owner_user_id": user_id}})

        elif context_scope == "organization" and strict_scope:
            filters.append({"terms": {"access_scope": ["organization", "system"]}})

        if category:
            filters.append({"term": {"category_id": category}})

        if file_type:
            filters.append({"term": {"file_type": file_type}})

        if time_range in {"7d", "30d"}:
            filters.append(
                {
                    "range": {
                        "indexed_at": {
                            "gte": f"now-{time_range}",
                        }
                    }
                }
            )

        return filters
