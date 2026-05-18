from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.core.exceptions import ContentPolicyViolationException, IngestionException, PromptInjectionException
from app.schemas.ingest import AllowedFileType
from app.services.chunking_service import Chunk, ChunkingService
from app.services.content_policy_service import ContentPolicyService
from app.services.embedding_service import EmbeddingService
from app.services.file_parser_service import FileParserService
from app.services.opensearch_service import OpenSearchService
from app.services.pinecone_service import PineconeService, PineconeVector

log = structlog.get_logger(__name__)


@dataclass
class IngestionInput(AgentInput):
    document_id: str = ""
    file_path: str = ""
    file_name: str = ""
    file_type: str = ""  # runtime value — validated by AllowedFileType at API layer
    access_scope: str = "organization"
    uploaded_by: Optional[str] = None
    project_id: Optional[str] = None
    folder_id: Optional[str] = None
    folder_path_ids: list[str] = field(default_factory=list)
    category_id: Optional[str] = None
    role_ids: list[str] = field(default_factory=list)
    is_reindex: bool = True  # Luôn xóa chunks cũ trước khi index mới


@dataclass
class IngestionOutput(AgentOutput):
    document_id: str = ""
    chunks_count: int = 0
    chunks: list[dict[str, Any]] = field(default_factory=list)


class IngestionAgent(BaseAgent):
    """
    Full document ingestion pipeline:
    parse → policy check → chunk → embed → [delete old] → dual index (Pinecone + OpenSearch)

    Rules:
    - Luôn xóa chunks cũ (is_reindex=True) để tránh stale data khi upload version mới.
    - Pinecone và OpenSearch được index SONG SONG bằng asyncio.gather().
    - ContentPolicyViolation không retry — lỗi content, không phải technical.
    - return_exceptions=True trong delete step để không fail nếu chưa có chunks cũ.
    """

    async def run(self, input: AgentInput) -> IngestionOutput:
        assert isinstance(input, IngestionInput), "Expected IngestionInput"

        log.info(
            "ingestion_agent.started",
            document_id=input.document_id,
            organization_id=input.organization_id,
            file_type=input.file_type,
        )

        try:
            # ── Step 1: Parse file ─────────────────────────────────────────
            raw_text = await FileParserService.parse(input.file_path, input.file_type)
            if not raw_text.strip():
                raise IngestionException(f"Parsed empty content from {input.file_path}")

            log.debug("ingestion_agent.parsed", chars=len(raw_text))

            # ── Step 2: Content policy check ───────────────────────────────
            try:
                policy_result = await ContentPolicyService.check_document(
                    text=raw_text,
                    document_id=input.document_id,
                    organization_id=input.organization_id,
                    file_name=input.file_name,
                )
                if policy_result.warnings:
                    log.info(
                        "ingestion_agent.policy_warnings",
                        document_id=input.document_id,
                        warnings=policy_result.warnings,
                    )
            except (ContentPolicyViolationException, PromptInjectionException):
                raise
            except Exception as exc:
                log.error(
                    "ingestion_agent.policy_check_error",
                    document_id=input.document_id,
                    error=str(exc),
                )

            # ── Step 3: Chunk text ─────────────────────────────────────────
            base_metadata = {
                "document_id": input.document_id,
                "organization_id": input.organization_id,
                "file_name": input.file_name,
                "file_type": input.file_type,
                "access_scope": input.access_scope,
                "owner_user_id": input.uploaded_by or "",
                "project_id": input.project_id or "",
                "folder_id": input.folder_id or "",
                "folder_path_ids": input.folder_path_ids,
                "category_id": input.category_id or "",
                "role_ids": input.role_ids,
                "source_type": "text",
                "ocr_confidence": 1.0,
            }
            chunks: list[Chunk] = ChunkingService.chunk_text(raw_text, base_metadata)
            log.info("ingestion_agent.chunked", chunks_count=len(chunks))

            # ── Step 4: Batch embed ────────────────────────────────────────
            texts = [c.content for c in chunks]
            embeddings = await EmbeddingService.embed_batch(
                texts,
                organization_id=input.organization_id,
            )

            if len(embeddings) != len(chunks):
                raise IngestionException("Embedding count mismatch with chunk count")

            # ── Step 5: Xóa chunks cũ TRƯỚC (tránh stale data) ────────────
            namespace = f"org_{input.organization_id}"
            if input.is_reindex:
                await asyncio.gather(
                    PineconeService.delete_by_filter(
                        namespace=namespace,
                        filter={"document_id": {"$eq": input.document_id}},
                    ),
                    OpenSearchService.delete_by_document_id(
                        organization_id=input.organization_id,
                        document_id=input.document_id,
                    ),
                    return_exceptions=True,  # Không fail nếu chưa có chunks cũ
                )
                log.debug("ingestion_agent.old_chunks_deleted", document_id=input.document_id)

            # ── Step 6: Build Pinecone vectors ─────────────────────────────
            indexed_at = datetime.now(timezone.utc).isoformat()
            vectors = [
                PineconeVector(
                    id=f"{input.document_id}_{chunk.chunk_index}",
                    values=embeddings[i],
                    metadata={
                        **chunk.metadata,
                        "content": chunk.content,
                        "token_count": chunk.token_count,
                        "indexed_at": indexed_at,
                    },
                )
                for i, chunk in enumerate(chunks)
            ]

            # ── Step 7: Dual index SONG SONG ───────────────────────────────
            await asyncio.gather(
                PineconeService.upsert(vectors=vectors, namespace=namespace),
                OpenSearchService.bulk_index(
                    organization_id=input.organization_id,
                    document_id=input.document_id,
                    chunks=chunks,
                ),
            )

            log.info(
                "ingestion_agent.upserted",
                document_id=input.document_id,
                namespace=namespace,
                vectors=len(vectors),
            )

            chunk_dicts = [
                {
                    "chunk_index": c.chunk_index,
                    "content": c.content,
                    "token_count": c.token_count,
                    "metadata": c.metadata,
                }
                for c in chunks
            ]

            return IngestionOutput(
                success=True,
                document_id=input.document_id,
                chunks_count=len(chunks),
                chunks=chunk_dicts,
            )

        except (IngestionException, ContentPolicyViolationException, PromptInjectionException):
            raise
        except Exception as exc:
            log.error("ingestion_agent.failed", document_id=input.document_id, error=str(exc))
            raise IngestionException(f"Ingestion pipeline failed: {exc}") from exc
