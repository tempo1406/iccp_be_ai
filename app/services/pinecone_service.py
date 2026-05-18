from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import structlog
from pinecone import Pinecone, ServerlessSpec

from app.core.config import settings
from app.core.exceptions import VectorStoreException

log = structlog.get_logger(__name__)

_BATCH_SIZE = 100


@dataclass
class PineconeVector:
    id: str
    values: list[float]
    metadata: dict[str, Any]


@dataclass
class ScoredVector:
    id: str
    score: float
    metadata: dict[str, Any]


class PineconeService:
    _client: Optional[Pinecone] = None
    _index = None

    @classmethod
    async def initialize(cls) -> None:
        """Initialize Pinecone client and ensure index exists."""
        try:
            cls._client = Pinecone(api_key=settings.PINECONE_API_KEY)
            existing = [idx.name for idx in cls._client.list_indexes()]
            if settings.PINECONE_INDEX_NAME not in existing:
                cls._client.create_index(
                    name=settings.PINECONE_INDEX_NAME,
                    dimension=settings.GEMINI_EMBEDDING_OUTPUT_DIMENSION,
                    metric="cosine",
                    spec=ServerlessSpec(cloud="aws", region="us-east-1"),
                )
                log.info("pinecone.index_created", index=settings.PINECONE_INDEX_NAME)
            cls._index = cls._client.Index(settings.PINECONE_INDEX_NAME)
            log.info("pinecone.index_ready", index=settings.PINECONE_INDEX_NAME)
        except Exception as exc:
            raise VectorStoreException(f"Failed to initialize Pinecone: {exc}") from exc

    @classmethod
    def _get_index(cls):
        if cls._index is None:
            raise VectorStoreException("Pinecone not initialized. Call PineconeService.initialize() first.")
        return cls._index

    @classmethod
    async def upsert(cls, vectors: list[PineconeVector], namespace: str) -> None:
        """Upsert vectors to Pinecone in batches of 100."""
        if not vectors:
            return
        index = cls._get_index()
        try:
            for i in range(0, len(vectors), _BATCH_SIZE):
                batch = vectors[i : i + _BATCH_SIZE]
                records = [
                    {"id": v.id, "values": v.values, "metadata": v.metadata}
                    for v in batch
                ]
                await asyncio.to_thread(index.upsert, vectors=records, namespace=namespace)
                log.debug("pinecone.upsert_batch", namespace=namespace, count=len(batch))
        except Exception as exc:
            raise VectorStoreException(f"Pinecone upsert failed: {exc}") from exc

    @classmethod
    async def query(
        cls,
        vector: list[float],
        namespace: str,
        filter: Optional[dict] = None,
        top_k: int = 6,
    ) -> list[ScoredVector]:
        """Query Pinecone for similar vectors."""
        index = cls._get_index()
        try:
            results = await asyncio.to_thread(
                index.query,
                vector=vector,
                namespace=namespace,
                filter=filter,
                top_k=top_k,
                include_metadata=True,
                include_values=False,
            )
            return [
                ScoredVector(id=m.id, score=m.score, metadata=m.metadata or {})
                for m in results.matches
            ]
        except Exception as exc:
            raise VectorStoreException(f"Pinecone query failed: {exc}") from exc

    @classmethod
    async def delete_by_filter(cls, namespace: str, filter: dict) -> None:
        """Delete all vectors matching a filter in a namespace."""
        index = cls._get_index()
        try:
            await asyncio.to_thread(
                index.delete,
                namespace=namespace,
                filter=filter,
            )
            log.info("pinecone.deleted_by_filter", namespace=namespace, filter=filter)
        except Exception as exc:
            raise VectorStoreException(f"Pinecone delete failed: {exc}") from exc

    @classmethod
    async def delete_by_ids(cls, ids: list[str], namespace: str) -> None:
        """Delete vectors by IDs."""
        if not ids:
            return
        index = cls._get_index()
        try:
            for i in range(0, len(ids), _BATCH_SIZE):
                batch = ids[i : i + _BATCH_SIZE]
                await asyncio.to_thread(index.delete, ids=batch, namespace=namespace)
        except Exception as exc:
            raise VectorStoreException(f"Pinecone delete by ids failed: {exc}") from exc
