from __future__ import annotations

import asyncio
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.clients.be_core_client import BeCoreClient
from app.core.config import settings
from app.services.embedding_service import EmbeddingService
from app.services.opensearch_service import OpenSearchHit, OpenSearchService
from app.services.pinecone_service import PineconeService, ScoredVector
from app.services.query_expansion_service import QueryExpansionService

log = structlog.get_logger(__name__)

_COMMON_STOP_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "cho",
    "co",
    "cua",
    "duoc",
    "for",
    "from",
    "in",
    "is",
    "la",
    "mot",
    "nhung",
    "of",
    "on",
    "or",
    "tai",
    "tai_lieu",
    "the",
    "to",
    "trong",
    "tu",
    "ve",
    "và",
    "được",
    "có",
    "của",
    "là",
    "một",
    "những",
    "tài",
    "trong",
    "từ",
    "về",
}


@dataclass
class RetrievedChunk:
    vector_id: str
    document_id: str
    chunk_index: int
    content: str
    score: float
    file_name: str
    file_type: str
    access_scope: str
    project_id: Optional[str] = None
    token_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalInput(AgentInput):
    query: str = ""
    top_k: int = 0
    context_scope: str = "organization"
    context_id: Optional[str] = None
    context_options: dict[str, Any] = field(default_factory=dict)
    role_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)
    bearer_token: Optional[str] = None

    def __post_init__(self) -> None:
        if self.top_k == 0:
            self.top_k = settings.RETRIEVAL_TOP_K
        self.context_options = self._normalize_context_options(self.context_options)

    @staticmethod
    def _normalize_context_options(options: dict[str, Any] | None) -> dict[str, Any]:
        raw = options or {}

        def _bool(name: str, default: bool = False) -> bool:
            value = raw.get(name, default)
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() == "true"
            return bool(value)

        def _str(name: str) -> str:
            return str(raw.get(name) or "").strip()

        def _str_list(name: str) -> list[str]:
            value = raw.get(name) or []
            if isinstance(value, str):
                return [item.strip() for item in value.split(",") if item.strip()]
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        normalized_time_range = _str("time_range")
        if normalized_time_range not in {"7d", "30d", "all"}:
            normalized_time_range = ""

        return {
            "strict_scope": _bool("strict_scope"),
            "include_subfolders": _bool("include_subfolders"),
            "prefer_owner": _bool("prefer_owner", default=True),
            "include_shared_docs": _bool("include_shared_docs"),
            "time_range": normalized_time_range,
            "category": _str("category"),
            "file_type": _str("file_type"),
            "document_ids": _str_list("document_ids"),
        }


@dataclass
class RetrievalOutput(AgentOutput):
    chunks: list[RetrievedChunk] = field(default_factory=list)
    query_embedding: list[float] = field(default_factory=list)


class RetrievalAgent(BaseAgent):
    """
    Hybrid retrieval: Pinecone (semantic) + OpenSearch (lexical) + Runtime ACL check.

    Flow:
    1. Embed query
    2. Query Pinecone + OpenSearch SONG SONG
    3. RRF Fusion — kết hợp kết quả từ 2 nguồn
    4. batch-access-check với be_core (runtime ACL — single source of truth)
    5. Filter bỏ chunks bị denied
    6. Optional rerank

    Rules:
    - Fail safe: ACL check fail → allowed_ids = set() → trả về rỗng.
    - Không fallback sang chấp nhận tất cả chunks khi ACL check fail.
    - top_k * 2 candidates trước RRF để có đủ sau filter.
    """

    async def run(self, input: AgentInput) -> RetrievalOutput:
        assert isinstance(input, RetrievalInput), "Expected RetrievalInput"

        log.debug(
            "retrieval_agent.started",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            query_preview=input.query[:80],
            top_k=input.top_k,
            context_scope=input.context_scope,
            context_id=input.context_id,
            context_options=input.context_options,
        )

        expansion = await QueryExpansionService.expand(
            input.query,
            organization_id=input.organization_id,
        )
        search_queries = expansion.search_queries or [input.query]
        input.extra["retrieval_search_queries"] = search_queries
        input.extra["retrieval_query_language"] = expansion.detected_language

        # ── Step 1: Embed query variants ───────────────────────────────────
        query_embeddings = await EmbeddingService.embed_batch(
            search_queries,
            organization_id=input.organization_id,
        )
        query_embedding = query_embeddings[0] if query_embeddings else []

        # ── Step 2: Query Pinecone + OpenSearch song song ──────────────────
        pinecone_filter = self._build_pinecone_filter(input)
        namespace = f"org_{input.organization_id}"
        candidates = input.top_k * 2  # Lấy nhiều hơn để RRF filter tốt hơn
        log.debug(
            "retrieval_agent.query_started",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            namespace=namespace,
            candidate_limit=candidates,
            pinecone_filter=pinecone_filter,
            query_variants=len(search_queries),
        )

        pinecone_batches, opensearch_batches = await asyncio.gather(
            asyncio.gather(
                *[
                    PineconeService.query(
                        vector=embedding,
                        namespace=namespace,
                        filter=pinecone_filter,
                        top_k=candidates,
                    )
                    for embedding in query_embeddings
                    if embedding
                ]
            ),
            asyncio.gather(
                *[
                    OpenSearchService.search(
                        organization_id=input.organization_id,
                        query=query_variant,
                        top_k=candidates,
                        context_scope=input.context_scope,
                        context_id=input.context_id,
                        project_ids=input.project_ids,
                        user_id=input.user_id,
                        context_options=input.context_options,
                    )
                    for query_variant in search_queries
                ]
            ),
        )
        pinecone_results = self._merge_scored_vectors(pinecone_batches, top_k=candidates)
        opensearch_results = self._merge_opensearch_hits(opensearch_batches, top_k=candidates)

        log.info(
            "retrieval_agent.candidates_ready",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            context_scope=input.context_scope,
            context_id=input.context_id,
            top_k=input.top_k,
            candidate_limit=candidates,
            pinecone_candidates=len(pinecone_results),
            opensearch_candidates=len(opensearch_results),
        )

        # ── Step 3: RRF Fusion ─────────────────────────────────────────────
        chunks = self._rrf_fusion(pinecone_results, opensearch_results, top_k=candidates)
        log.info(
            "retrieval_agent.rrf_fused",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            fused_chunks=len(chunks),
        )

        if not chunks:
            log.info(
                "retrieval_agent.empty_after_fusion",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
            )
            return RetrievalOutput(success=True, chunks=[], query_embedding=query_embedding)

        chunks = self._apply_scope_post_filter(input, chunks)
        log.info(
            "retrieval_agent.after_scope_filter",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            context_scope=input.context_scope,
            context_id=input.context_id,
            context_options=input.context_options,
            after_scope=len(chunks),
        )

        if not chunks:
            log.info(
                "retrieval_agent.empty_after_scope_filter",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
            )
            return RetrievalOutput(success=True, chunks=[], query_embedding=query_embedding)

        # ── Step 4: Runtime ACL check với be_core ─────────────────────────
        unique_doc_ids = list({c.document_id for c in chunks})
        log.info(
            "retrieval_agent.acl_check_started",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            unique_candidate_docs=len(unique_doc_ids),
            role_ids_count=len(input.role_ids),
            project_ids_count=len(input.project_ids),
            has_bearer_token=bool(input.bearer_token),
        )

        try:
            acl_result = await BeCoreClient.batch_access_check(
                user_id=input.user_id,
                organization_id=input.organization_id,
                document_ids=unique_doc_ids,
                role_ids=input.role_ids,
                project_ids=input.project_ids,
                bearer_token=input.bearer_token,
            )
            allowed_ids = set(acl_result.get("allowed", []))
            denied_ids = acl_result.get("denied", [])
            log.info(
                "retrieval_agent.acl_check_completed",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
                allowed_docs=len(allowed_ids),
                denied_docs=len(denied_ids),
            )
        except Exception as exc:
            # Fail safe: nếu be_core không trả lời → từ chối tất cả
            log.error(
                "retrieval.acl_check_failed",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
                error=str(exc),
                candidate_docs=len(unique_doc_ids),
            )
            allowed_ids = set()

        # ── Step 5: Filter bỏ chunks bị denied ────────────────────────────
        before_acl = len(chunks)
        chunks = [c for c in chunks if c.document_id in allowed_ids]
        log.info(
            "retrieval_agent.after_acl_filter",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            before_acl=before_acl,
            after_acl=len(chunks),
        )

        chunks = self._rerank_by_hybrid_evidence(input, chunks)
        log.info(
            "retrieval_agent.hybrid_reranked",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            reranked_chunks=len(chunks),
            top_documents=list({c.document_id for c in chunks[:3]}),
        )

        # ── Score threshold filter ─────────────────────────────────────────
        threshold = settings.RETRIEVAL_SCORE_THRESHOLD
        if threshold > 0 and chunks:
            before_threshold = len(chunks)
            chunks = [c for c in chunks if c.score >= threshold]
            if len(chunks) < before_threshold:
                log.info(
                    "retrieval_agent.score_threshold_applied",
                    trace_id=input.trace_id,
                    organization_id=input.organization_id,
                    threshold=threshold,
                    before=before_threshold,
                    after=len(chunks),
                )

        # ── Step 6: Optional rerank ────────────────────────────────────────
        if settings.ENABLE_RERANKING and len(chunks) > 1:
            chunks = await self._rerank(input.query, chunks)
            log.info(
                "retrieval_agent.rerank_completed",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
                reranked_chunks=len(chunks),
            )

        log.info(
            "retrieval_agent.complete",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            total_candidates=len(unique_doc_ids),
            after_acl=len(chunks),
            context_scope=input.context_scope,
            returned_chunks=min(len(chunks), input.top_k),
        )

        return RetrievalOutput(
            success=True,
            chunks=chunks[: input.top_k],
            query_embedding=query_embedding,
        )

    def _build_pinecone_filter(self, input: RetrievalInput) -> dict[str, Any]:
        """Pre-filter scope trong Pinecone. Kết quả vẫn cần ACL check sau."""
        context_options = input.context_options
        strict_scope = bool(context_options.get("strict_scope"))
        include_subfolders = bool(context_options.get("include_subfolders"))
        include_shared_docs = bool(context_options.get("include_shared_docs"))
        category = (context_options.get("category") or "").strip()
        file_type = (context_options.get("file_type") or "").strip().lower()

        clauses: list[dict[str, Any]] = [
            {"organization_id": {"$eq": input.organization_id}},
        ]

        if input.context_scope == "document" and input.context_id:
            clauses.append({"document_id": {"$eq": input.context_id}})
        elif input.context_scope == "custom_docs":
            document_ids = context_options.get("document_ids") or []
            if document_ids:
                clauses.append({"document_id": {"$in": document_ids}})
        elif input.context_scope == "project" and input.context_id and strict_scope:
            clauses.append({"project_id": {"$eq": input.context_id}})
        elif input.context_scope == "folder" and input.context_id and not include_subfolders:
            clauses.append({"folder_id": {"$eq": input.context_id}})
        elif input.context_scope == "my_docs" and input.user_id and not include_shared_docs:
            clauses.append({"owner_user_id": {"$eq": input.user_id}})

        if strict_scope and input.context_scope == "organization":
            clauses.append({"access_scope": {"$in": ["organization", "system"]}})

        if category:
            clauses.append({"category_id": {"$eq": category}})

        if file_type:
            clauses.append({"file_type": {"$eq": file_type}})

        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}

    def _apply_scope_post_filter(
        self,
        input: RetrievalInput,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        filtered = [chunk for chunk in chunks if self._matches_context_filters(input, chunk)]
        if not filtered:
            return filtered

        if input.context_scope == "my_docs" and input.context_options.get("prefer_owner"):
            filtered.sort(
                key=lambda chunk: self._owner_matches(input.user_id, chunk),
                reverse=True,
            )

        return filtered

    def _matches_context_filters(
        self,
        input: RetrievalInput,
        chunk: RetrievedChunk,
    ) -> bool:
        context_options = input.context_options
        strict_scope = bool(context_options.get("strict_scope"))
        category = (context_options.get("category") or "").strip()
        file_type = (context_options.get("file_type") or "").strip().lower()
        time_range = (context_options.get("time_range") or "").strip()

        if input.context_scope == "document" and input.context_id:
            if chunk.document_id != input.context_id:
                return False

        if input.context_scope == "custom_docs":
            document_ids = set(context_options.get("document_ids") or [])
            if not document_ids or chunk.document_id not in document_ids:
                return False

        if input.context_scope == "project" and input.context_id:
            if not self._matches_project_scope(input.context_id, strict_scope, chunk):
                return False

        if input.context_scope == "folder" and input.context_id:
            if not self._matches_folder_scope(
                folder_id=input.context_id,
                include_subfolders=bool(context_options.get("include_subfolders")),
                chunk=chunk,
            ):
                return False

        if input.context_scope == "my_docs":
            if not self._matches_my_docs_scope(
                user_id=input.user_id,
                include_shared_docs=bool(context_options.get("include_shared_docs")),
                chunk=chunk,
            ):
                return False

        if input.context_scope == "organization" and strict_scope:
            if chunk.access_scope not in {"organization", "system"}:
                return False

        if category:
            if str(chunk.metadata.get("category_id") or "").strip() != category:
                return False

        if file_type:
            chunk_file_type = str(chunk.file_type or chunk.metadata.get("file_type") or "").strip().lower()
            if chunk_file_type != file_type:
                return False

        if not self._matches_time_range(time_range, chunk):
            return False

        return True

    def _matches_project_scope(
        self,
        context_id: str,
        strict_scope: bool,
        chunk: RetrievedChunk,
    ) -> bool:
        chunk_project_id = str(chunk.project_id or chunk.metadata.get("project_id") or "").strip()
        if strict_scope:
            return chunk_project_id == context_id
        return chunk_project_id == context_id or chunk.access_scope in {"organization", "system"}

    def _matches_folder_scope(
        self,
        folder_id: str,
        include_subfolders: bool,
        chunk: RetrievedChunk,
    ) -> bool:
        chunk_folder_id = str(chunk.metadata.get("folder_id") or "").strip()
        if chunk_folder_id == folder_id:
            return True
        if not include_subfolders:
            return False
        return folder_id in self._metadata_list(chunk.metadata.get("folder_path_ids"))

    def _matches_my_docs_scope(
        self,
        user_id: str,
        include_shared_docs: bool,
        chunk: RetrievedChunk,
    ) -> bool:
        if not user_id:
            return True

        if self._owner_matches(user_id, chunk):
            return True

        if not include_shared_docs:
            return False

        if chunk.access_scope in {"user", "role", "project"}:
            return True

        return bool(str(chunk.project_id or chunk.metadata.get("project_id") or "").strip())

    def _matches_time_range(self, time_range: str, chunk: RetrievedChunk) -> bool:
        if not time_range or time_range == "all":
            return True

        indexed_at = self._parse_datetime(chunk.metadata.get("indexed_at"))
        if indexed_at is None:
            return False

        if time_range == "7d":
            threshold = datetime.now(timezone.utc) - timedelta(days=7)
        elif time_range == "30d":
            threshold = datetime.now(timezone.utc) - timedelta(days=30)
        else:
            return True

        return indexed_at >= threshold

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None

        if isinstance(value, datetime):
            dt = value
        else:
            text = str(value).strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = f"{text[:-1]}+00:00"
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None

        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _metadata_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return []
            if "," in normalized:
                return [part.strip() for part in normalized.split(",") if part.strip()]
            return [normalized]
        return []

    def _owner_matches(self, user_id: str, chunk: RetrievedChunk) -> bool:
        if not user_id:
            return False
        owner_user_id = str(chunk.metadata.get("owner_user_id") or "").strip()
        return owner_user_id == user_id

    def _rrf_fusion(
        self,
        pinecone_results: list[ScoredVector],
        opensearch_results: list[OpenSearchHit],
        top_k: int,
        k: int = 60,  # RRF constant — giá trị chuẩn từ paper
    ) -> list[RetrievedChunk]:
        """
        Reciprocal Rank Fusion.
        score_rrf(d) = Σ 1 / (k + rank_i(d))
        """
        scores: dict[str, float] = {}
        chunks_map: dict[str, RetrievedChunk] = {}

        # Pinecone results
        for rank, sv in enumerate(pinecone_results):
            chunk = self._pinecone_to_chunk(sv)
            chunk_key = f"{chunk.document_id}_{chunk.chunk_index}"
            scores[chunk_key] = scores.get(chunk_key, 0.0) + 1.0 / (k + rank + 1)
            chunk.metadata["score_vector"] = sv.score
            chunks_map[chunk_key] = chunk

        # OpenSearch results
        for rank, hit in enumerate(opensearch_results):
            chunk_key = f"{hit.document_id}_{hit.chunk_index}"
            scores[chunk_key] = scores.get(chunk_key, 0.0) + 1.0 / (k + rank + 1)
            if chunk_key in chunks_map:
                chunks_map[chunk_key].metadata["score_lexical"] = hit.score
            else:
                chunk = self._opensearch_to_chunk(hit)
                chunks_map[chunk_key] = chunk

        # Sort by RRF score
        sorted_keys = sorted(scores.keys(), key=lambda k_: scores[k_], reverse=True)
        result = []
        for key in sorted_keys[:top_k]:
            chunk = chunks_map[key]
            chunk.score = scores[key]
            chunk.metadata["final_score"] = scores[key]
            result.append(chunk)

        return result

    def _pinecone_to_chunk(self, sv: ScoredVector) -> RetrievedChunk:
        meta = sv.metadata
        return RetrievedChunk(
            vector_id=sv.id,
            document_id=meta.get("document_id", ""),
            chunk_index=int(meta.get("chunk_index", 0)),
            content=meta.get("content", ""),
            score=sv.score,
            file_name=meta.get("file_name", ""),
            file_type=meta.get("file_type", ""),
            access_scope=meta.get("access_scope", "organization"),
            project_id=meta.get("project_id") or None,
            token_count=int(meta.get("token_count", 0)),
            metadata={
                **meta,
                "score_vector": sv.score,
                "score_lexical": 0.0,
                "score_graph": 0.0,
            },
        )

    def _opensearch_to_chunk(self, hit: OpenSearchHit) -> RetrievedChunk:
        return RetrievedChunk(
            vector_id=f"{hit.document_id}_{hit.chunk_index}",
            document_id=hit.document_id,
            chunk_index=hit.chunk_index,
            content=hit.content,
            score=hit.score,
            file_name=hit.file_name,
            file_type=hit.file_type,
            access_scope=hit.access_scope,
            project_id=hit.project_id,
            token_count=0,
            metadata={
                **hit.metadata,
                "score_lexical": hit.score,
                "score_vector": 0.0,
                "score_graph": 0.0,
            },
        )

    async def _rerank(
        self, query: str, chunks: list[RetrievedChunk]
    ) -> list[RetrievedChunk]:
        """Cross-encoder reranking. Simple score-based sort for now."""
        log.debug("retrieval_agent.reranking", chunks=len(chunks))
        return sorted(chunks, key=lambda c: c.score, reverse=True)

    def _rerank_by_hybrid_evidence(
        self,
        input: RetrievalInput,
        chunks: list[RetrievedChunk],
    ) -> list[RetrievedChunk]:
        """Deterministic rerank based on hybrid retrieval signals plus lexical evidence."""
        if len(chunks) <= 1:
            return chunks

        query_variants = input.extra.get("retrieval_search_queries") or [input.query]
        query_tokens = set().union(*(self._normalize_tokens(query) for query in query_variants))
        max_final = max(
            float(chunk.metadata.get("final_score", chunk.score) or chunk.score)
            for chunk in chunks
        ) or 1.0
        max_vector = max(float(chunk.metadata.get("score_vector", 0.0) or 0.0) for chunk in chunks)
        max_lexical = max(float(chunk.metadata.get("score_lexical", 0.0) or 0.0) for chunk in chunks)
        prefer_owner = (
            input.context_scope == "my_docs"
            and bool(input.context_options.get("prefer_owner"))
            and bool(input.user_id)
        )

        def ranking_score(chunk: RetrievedChunk) -> float:
            final_signal = self._normalized_signal(
                float(chunk.metadata.get("final_score", chunk.score) or chunk.score),
                max_final,
            )
            vector_signal = self._normalized_signal(
                float(chunk.metadata.get("score_vector", 0.0) or 0.0),
                max_vector,
            )
            lexical_signal = self._normalized_signal(
                float(chunk.metadata.get("score_lexical", 0.0) or 0.0),
                max_lexical,
            )
            overlap_signal = self._token_overlap(
                query_tokens,
                self._normalize_tokens(f"{chunk.file_name} {chunk.content}"),
            )
            owner_signal = (
                1.0
                if prefer_owner and self._owner_matches(input.user_id, chunk)
                else 0.0
            )
            if overlap_signal <= 0.01 and lexical_signal <= 0.01 and vector_signal >= 0.75:
                chunk.metadata["cross_language_vector_boost"] = True
                return (
                    (0.55 * final_signal)
                    + (0.35 * vector_signal)
                    + (0.10 * owner_signal)
                )
            return (
                (0.40 * final_signal)
                + (0.30 * overlap_signal)
                + (0.15 * vector_signal)
                + (0.15 * lexical_signal)
                + (0.05 * owner_signal)
            )

        ranked_chunks = sorted(
            chunks,
            key=lambda chunk: (
                ranking_score(chunk),
                float(chunk.metadata.get("score_lexical", 0.0) or 0.0),
                float(chunk.metadata.get("score_vector", 0.0) or 0.0),
                float(chunk.metadata.get("final_score", chunk.score) or chunk.score),
            ),
            reverse=True,
        )

        for chunk in ranked_chunks:
            evidence_score = ranking_score(chunk)
            chunk.metadata["evidence_rank_score"] = round(evidence_score, 4)
            # Promote the post-rerank evidence score into chunk.score so downstream
            # thresholding and citation relevance operate on a 0..1 scale instead
            # of the tiny raw RRF score (~0.01-0.03).
            chunk.score = evidence_score
            if prefer_owner:
                chunk.metadata["owner_match"] = self._owner_matches(input.user_id, chunk)

        return ranked_chunks

    def _token_overlap(self, query_tokens: set[str], candidate_tokens: set[str]) -> float:
        if not query_tokens or not candidate_tokens:
            return 0.0
        return len(query_tokens & candidate_tokens) / len(query_tokens)

    def _normalize_tokens(self, text: str) -> set[str]:
        normalized = unicodedata.normalize("NFKD", (text or "").lower())
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[_\-.]+", " ", ascii_text)
        return {
            token
            for token in re.findall(r"[a-z0-9]+", cleaned)
            if len(token) >= 2 and token not in _COMMON_STOP_WORDS
        }

    def _normalized_signal(self, value: float, upper_bound: float) -> float:
        if upper_bound <= 0:
            return 0.0
        return max(0.0, min(1.0, value / upper_bound))

    def _merge_scored_vectors(
        self,
        batches: list[list[ScoredVector]],
        *,
        top_k: int,
        k: int = 60,
    ) -> list[ScoredVector]:
        rank_scores: dict[str, float] = {}
        merged: dict[str, ScoredVector] = {}

        for batch in batches:
            for rank, vector in enumerate(batch):
                key = vector.id
                rank_scores[key] = rank_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                current = merged.get(key)
                if current is None or vector.score > current.score:
                    merged[key] = vector

        ordered_keys = sorted(
            rank_scores.keys(),
            key=lambda key: (rank_scores[key], merged[key].score),
            reverse=True,
        )
        return [merged[key] for key in ordered_keys[:top_k]]

    def _merge_opensearch_hits(
        self,
        batches: list[list[OpenSearchHit]],
        *,
        top_k: int,
        k: int = 60,
    ) -> list[OpenSearchHit]:
        rank_scores: dict[str, float] = {}
        merged: dict[str, OpenSearchHit] = {}

        for batch in batches:
            for rank, hit in enumerate(batch):
                key = f"{hit.document_id}_{hit.chunk_index}"
                rank_scores[key] = rank_scores.get(key, 0.0) + 1.0 / (k + rank + 1)
                current = merged.get(key)
                if current is None or hit.score > current.score:
                    merged[key] = hit

        ordered_keys = sorted(
            rank_scores.keys(),
            key=lambda key: (rank_scores[key], merged[key].score),
            reverse=True,
        )
        return [merged[key] for key in ordered_keys[:top_k]]
