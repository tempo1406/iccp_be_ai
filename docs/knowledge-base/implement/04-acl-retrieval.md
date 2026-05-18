# 04 — be_ai: ACL-Aware Retrieval

> **Repo:** `iccp_be_ai`
> **Prerequisite:** File 01 (`POST /internal/documents/batch-access-check`), File 03 (OpenSearchService)
> **Files sửa:** `app/agents/retrieval_agent.py`, `app/agents/orchestrator/orchestrator.py`, `app/api/v1/messages.py`

---

## Mục tiêu

1. **`messages.py`** — truyền `role_ids` và `project_ids` từ JWT vào `OrchestratorInput`
2. **`RetrievalAgent`** — query song song Pinecone + OpenSearch, sau đó gọi `batch-access-check` để filter chunks theo ACL live
3. **RRF Fusion** — kết hợp kết quả từ 2 nguồn bằng Reciprocal Rank Fusion

---

## Vấn đề hiện tại

```python
# retrieval_agent.py hiện tại — CHỈ filter theo org_id
def _build_filter(self, input: RetrievalInput) -> dict:
    return {"organization_id": {"$eq": input.organization_id}}
    # ❌ Mọi user trong org đều lấy được chunks của nhau
    # ❌ Không check isActive, không check access rules
```

---

## Bước 1 — Cập nhật `messages.py` để truyền role_ids

**File:** `app/api/v1/messages.py`

Hiện tại `messages.py` tạo `OrchestratorInput` nhưng không truyền `role_ids`. Cần lấy từ JWT:

```python
# Hiện tại (thiếu role_ids)
orchestrator_input = OrchestratorInput(
    organization_id=user.organization_id,
    user_id=user.user_id,
    ...
)

# Sau khi sửa — lấy role_ids và project_ids từ JWT payload
orchestrator_input = OrchestratorInput(
    organization_id=user.organization_id,
    user_id=user.user_id,
    conversation_id=conversation_id,
    user_message=body.content,
    mode=effective_mode,
    context_scope=conv.metadata.get("context_scope", "organization"),
    context_id=conv.metadata.get("context_id"),
    role_ids=user.role_ids or [],          # ← từ JWT payload
    project_ids=user.project_ids or [],    # ← từ JWT payload
    trace_id=trace_id,
)
```

Cần cập nhật `TokenPayload` schema để include `role_ids` và `project_ids`:

**File:** `app/core/security.py` hoặc `app/schemas/common.py`

```python
@dataclass
class TokenPayload:
    user_id: str
    organization_id: str
    email: str
    role_ids: list[str] = field(default_factory=list)       # ← thêm
    project_ids: list[str] = field(default_factory=list)    # ← thêm
```

> **Rule:** JWT từ be_core phải có `role_ids` và `project_ids` trong payload.
> Nếu be_core chưa đưa vào JWT, cần gọi `GET /v1/rbac/my-roles` sau khi introspect.
> Ưu tiên lấy từ JWT để tránh thêm HTTP round-trip.

---

## Bước 2 — Cập nhật `OrchestratorInput`

**File:** `app/agents/orchestrator/orchestrator.py`

```python
@dataclass
class OrchestratorInput:
    organization_id: str
    user_id: str
    conversation_id: str
    user_message: str
    mode: Literal["rag", "web", "hybrid"] = "rag"
    context_scope: str = "organization"
    context_id: Optional[str] = None
    role_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)   # ← thêm mới
    trace_id: str = ""
```

Cập nhật `_retrieve_context` để truyền thêm `project_ids`:

```python
# Trong _retrieve_context, thêm project_ids vào RetrievalInput
retrieval_out = await self._retrieval.run(
    RetrievalInput(
        organization_id=input.organization_id,
        user_id=input.user_id,
        trace_id=input.trace_id,
        query=input.user_message,
        context_scope=router_out.context_scope,
        context_id=router_out.context_id,
        role_ids=input.role_ids,
        project_ids=input.project_ids,   # ← thêm
    )
)
```

---

## Bước 3 — Cập nhật `RetrievalInput`

**File:** `app/agents/retrieval_agent.py`

```python
@dataclass
class RetrievalInput(AgentInput):
    query: str = ""
    top_k: int = 0
    context_scope: str = "organization"
    context_id: Optional[str] = None
    role_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)   # ← thêm mới

    def __post_init__(self) -> None:
        if self.top_k == 0:
            self.top_k = settings.RETRIEVAL_TOP_K
```

---

## Bước 4 — Cập nhật `RetrievalAgent` (phần quan trọng nhất)

**File:** `app/agents/retrieval_agent.py` — thay thế hoàn toàn `run()` và `_build_filter()`:

```python
class RetrievalAgent(BaseAgent):
    """
    Hybrid retrieval: Pinecone (semantic) + OpenSearch (lexical) + Runtime ACL check.

    Flow:
    1. Embed query
    2. Query Pinecone + OpenSearch SONG SONG
    3. RRF Fusion (kết hợp kết quả)
    4. batch-access-check với be_core (runtime ACL, single source of truth)
    5. Filter bỏ chunks bị denied
    6. Optional rerank
    """

    def __init__(self) -> None:
        self._be_core = BeCoreClient()

    async def run(self, input: AgentInput) -> RetrievalOutput:
        assert isinstance(input, RetrievalInput)

        # 1. Embed query
        query_embedding = await EmbeddingService.embed_one(input.query)

        # 2. Query Pinecone + OpenSearch song song
        pinecone_filter = self._build_pinecone_filter(input)
        namespace = f"org_{input.organization_id}"

        pinecone_results, opensearch_results = await asyncio.gather(
            PineconeService.query(
                vector=query_embedding,
                namespace=namespace,
                filter=pinecone_filter,
                top_k=input.top_k * 2,  # Lấy nhiều hơn để RRF filter tốt hơn
            ),
            OpenSearchService.search(
                organization_id=input.organization_id,
                query=input.query,
                top_k=input.top_k * 2,
                context_scope=input.context_scope,
                context_id=input.context_id,
                project_ids=input.project_ids,
            ),
        )

        # 3. RRF Fusion
        chunks = self._rrf_fusion(
            pinecone_results,
            opensearch_results,
            top_k=input.top_k,
        )

        if not chunks:
            return RetrievalOutput(success=True, chunks=[], query_embedding=query_embedding)

        # 4. Runtime ACL check với be_core (PHẢI làm — đây là source of truth)
        unique_doc_ids = list({c.document_id for c in chunks})
        try:
            acl_result = await self._be_core.batch_access_check(
                user_id=input.user_id,
                organization_id=input.organization_id,
                document_ids=unique_doc_ids,
                role_ids=input.role_ids,
                project_ids=input.project_ids,
            )
            allowed_ids = set(acl_result.get("allowed", []))
        except Exception as exc:
            # Nếu be_core không trả lời → fail safe: từ chối tất cả
            log.error("retrieval.acl_check_failed", error=str(exc))
            allowed_ids = set()

        # 5. Filter bỏ chunks bị denied
        chunks = [c for c in chunks if c.document_id in allowed_ids]

        # 6. Optional rerank
        if settings.ENABLE_RERANKING and len(chunks) > 1:
            chunks = await self._rerank(input.query, chunks)

        log.info(
            "retrieval.complete",
            organization_id=input.organization_id,
            total_candidates=len(unique_doc_ids),
            after_acl=len(chunks),
        )

        return RetrievalOutput(
            success=True,
            chunks=chunks[:input.top_k],
            query_embedding=query_embedding,
        )

    def _build_pinecone_filter(self, input: RetrievalInput) -> dict:
        """Pre-filter scope trong Pinecone. Kết quả vẫn cần ACL check sau."""
        base = {"organization_id": {"$eq": input.organization_id}}

        if input.context_scope == "document" and input.context_id:
            return {**base, "document_id": {"$eq": input.context_id}}

        if input.context_scope == "project" and input.context_id:
            return {
                **base,
                "$or": [
                    {"access_scope": {"$eq": "organization"}},
                    {"access_scope": {"$eq": "system"}},
                    {"project_id": {"$eq": input.context_id}},
                ],
            }

        # Default: organization scope
        return {
            **base,
            "$or": [
                {"access_scope": {"$eq": "organization"}},
                {"access_scope": {"$eq": "system"}},
            ],
        }

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
        Trong đó rank_i(d) là thứ hạng của document d trong result set i.
        """
        scores: dict[str, float] = {}
        chunks_map: dict[str, RetrievedChunk] = {}

        # Pinecone results
        for rank, sv in enumerate(pinecone_results):
            chunk = self._pinecone_to_chunk(sv, score_lexical=0.0)
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
                chunk.metadata["score_lexical"] = hit.score
                chunks_map[chunk_key] = chunk

        # Sort by RRF score
        sorted_keys = sorted(scores.keys(), key=lambda k: scores[k], reverse=True)
        result = []
        for key in sorted_keys[:top_k]:
            chunk = chunks_map[key]
            chunk.score = scores[key]  # final_score = RRF score
            chunk.metadata["final_score"] = scores[key]
            result.append(chunk)

        return result

    def _pinecone_to_chunk(self, sv: ScoredVector, score_lexical: float) -> RetrievedChunk:
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
                "score_lexical": score_lexical,
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
```

---

## Sơ đồ luồng ACL

```
User gửi message
    │
    ├─ JWT payload → role_ids, project_ids → OrchestratorInput
    │
    ▼
RetrievalAgent.run()
    │
    ├─ Pinecone (pre-filter: org + scope)    ──┐
    ├─ OpenSearch (pre-filter: org + scope)  ──┤ song song
    │                                           │
    ▼                                           ▼
    └───────────── RRF Fusion ─────────────────┘
                        │
                        ▼
    POST /internal/documents/batch-access-check (be_core)
    → Check live DB: isActive + ACL rules + role/project match
                        │
        ┌───────────────┴───────────────┐
        ▼                               ▼
    allowed_ids                    denied_ids
        │                               │
    Giữ lại                         Bỏ ra
        │
        ▼
    chunks đã qua ACL → ChatAgent
```

---

## Rule quan trọng

> **Fail safe:** Nếu `batch-access-check` gọi be_core thất bại (timeout, 5xx),
> `allowed_ids = set()` → tất cả chunks bị filter bỏ → trả về "không tìm thấy thông tin".
> **KHÔNG** fallback sang chấp nhận tất cả chunks khi ACL check fail.

---

## Checklist file này

- [ ] `TokenPayload` có `role_ids` và `project_ids`
- [ ] `messages.py` truyền `role_ids` và `project_ids` vào `OrchestratorInput`
- [ ] `RetrievalAgent` query Pinecone + OpenSearch song song
- [ ] RRF Fusion kết hợp kết quả từ 2 nguồn
- [ ] `batch-access-check` được gọi SAU khi có candidates
- [ ] Fail safe: ACL check fail → `allowed_ids = set()`
- [ ] `score_vector`, `score_lexical`, `final_score` có trong chunk metadata (cho citations)
