import pytest
from unittest.mock import AsyncMock, patch

from app.agents.retrieval_agent import RetrievedChunk, RetrievalAgent, RetrievalInput, RetrievalOutput
from app.services.pinecone_service import ScoredVector
from app.services.query_expansion_service import QueryExpansionResult


@pytest.mark.asyncio
async def test_retrieval_agent_returns_chunks():
    """RetrievalAgent should embed query and return ranked chunks."""
    mock_vectors = [
        ScoredVector(
            id="doc-1_0",
            score=0.92,
            metadata={
                "document_id": "doc-1",
                "chunk_index": 0,
                "content": "Chính sách nghỉ phép của công ty",
                "file_name": "hr_policy.pdf",
                "file_type": "pdf",
                "access_scope": "organization",
                "token_count": 50,
            },
        )
    ]

    with (
        patch("app.agents.retrieval_agent.QueryExpansionService.expand", AsyncMock(
            return_value=QueryExpansionResult(detected_language="vi", search_queries=["Quy định nghỉ phép là gì?"])
        )),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.query", AsyncMock(
            return_value=mock_vectors
        )),
        patch("app.services.opensearch_service.OpenSearchService.search", AsyncMock(return_value=[])),
        patch(
            "app.agents.retrieval_agent.BeCoreClient.batch_access_check",
            AsyncMock(return_value={"allowed": ["doc-1"], "denied": []}),
        ),
    ):
        agent = RetrievalAgent()
        result = await agent.run(
            RetrievalInput(
                organization_id="org-123",
                user_id="user-1",
                query="Quy định nghỉ phép là gì?",
                top_k=6,
            )
        )

    assert isinstance(result, RetrievalOutput)
    assert result.success is True
    assert len(result.chunks) == 1
    assert result.chunks[0].document_id == "doc-1"
    assert result.chunks[0].metadata["score_vector"] == 0.92


@pytest.mark.asyncio
async def test_retrieval_agent_project_scope_filter():
    """Non-strict project scope starts broad and relies on post-filter + ACL."""
    captured_filter = []

    async def mock_query(vector, namespace, filter, top_k):
        captured_filter.append(filter)
        return []

    with (
        patch("app.agents.retrieval_agent.QueryExpansionService.expand", AsyncMock(
            return_value=QueryExpansionResult(detected_language="en", search_queries=["project docs query"])
        )),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.query", AsyncMock(side_effect=mock_query)),
        patch("app.services.opensearch_service.OpenSearchService.search", AsyncMock(return_value=[])),
    ):
        agent = RetrievalAgent()
        await agent.run(
            RetrievalInput(
                organization_id="org-123",
                user_id="user-1",
                query="project docs query",
                context_scope="project",
                context_id="proj-456",
            )
        )

    assert len(captured_filter) == 1
    flt = captured_filter[0]
    assert flt["organization_id"] == {"$eq": "org-123"}
    assert "$or" not in flt


@pytest.mark.asyncio
async def test_retrieval_agent_custom_docs_scope_filter():
    """Custom document scope must constrain vector search to selected document ids."""
    captured_filter = []

    async def mock_query(vector, namespace, filter, top_k):
        captured_filter.append(filter)
        return []

    with (
        patch("app.agents.retrieval_agent.QueryExpansionService.expand", AsyncMock(
            return_value=QueryExpansionResult(detected_language="en", search_queries=["selected docs query"])
        )),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.query", AsyncMock(side_effect=mock_query)),
        patch("app.services.opensearch_service.OpenSearchService.search", AsyncMock(return_value=[])),
    ):
        agent = RetrievalAgent()
        await agent.run(
            RetrievalInput(
                organization_id="org-123",
                user_id="user-1",
                query="selected docs query",
                context_scope="custom_docs",
                context_options={"document_ids": ["doc-a", "doc-b"]},
            )
        )

    assert len(captured_filter) == 1
    flt = captured_filter[0]
    assert flt == {
        "$and": [
            {"organization_id": {"$eq": "org-123"}},
            {"document_id": {"$in": ["doc-a", "doc-b"]}},
        ]
    }


@pytest.mark.asyncio
async def test_retrieval_agent_keeps_hybrid_results_when_rrf_score_is_below_threshold():
    """Hybrid/RRF retrieval should not drop valid chunks just because raw RRF scores are tiny."""
    mock_vectors = [
        ScoredVector(
            id="doc-a_0",
            score=0.91,
            metadata={
                "document_id": "doc-a",
                "chunk_index": 0,
                "content": "Tài liệu onboarding cho nhân viên mới và quy trình làm việc nội bộ.",
                "file_name": "employee_onboarding.pdf",
                "file_type": "pdf",
                "access_scope": "organization",
                "token_count": 80,
            },
        ),
        ScoredVector(
            id="doc-b_0",
            score=0.88,
            metadata={
                "document_id": "doc-b",
                "chunk_index": 0,
                "content": "Chính sách onboarding nội bộ và hướng dẫn nhân sự.",
                "file_name": "hr_onboarding.pdf",
                "file_type": "pdf",
                "access_scope": "organization",
                "token_count": 70,
            },
        ),
    ]

    with (
        patch("app.agents.retrieval_agent.QueryExpansionService.expand", AsyncMock(
            return_value=QueryExpansionResult(detected_language="vi", search_queries=["quy trình onboarding nội bộ"])
        )),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.query", AsyncMock(
            return_value=mock_vectors
        )),
        patch("app.services.opensearch_service.OpenSearchService.search", AsyncMock(return_value=[])),
        patch(
            "app.agents.retrieval_agent.BeCoreClient.batch_access_check",
            AsyncMock(return_value={"allowed": ["doc-a", "doc-b"], "denied": []}),
        ),
    ):
        agent = RetrievalAgent()
        result = await agent.run(
            RetrievalInput(
                organization_id="org-123",
                user_id="user-1",
                query="quy trình onboarding nội bộ",
                top_k=8,
                context_scope="custom_docs",
                context_options={"document_ids": ["doc-a", "doc-b"], "strict_scope": True},
            )
        )

    assert result.success is True
    assert len(result.chunks) >= 1
    assert result.chunks[0].document_id in {"doc-a", "doc-b"}
    assert result.chunks[0].score >= 0.3
    assert result.chunks[0].metadata["final_score"] < 0.3
    assert result.chunks[0].metadata["evidence_rank_score"] >= 0.3


@pytest.mark.asyncio
async def test_retrieval_agent_namespace():
    """Pinecone query must use org_{organization_id} namespace."""
    captured_ns = []

    async def mock_query(vector, namespace, filter, top_k):
        captured_ns.append(namespace)
        return []

    with (
        patch("app.agents.retrieval_agent.QueryExpansionService.expand", AsyncMock(
            return_value=QueryExpansionResult(detected_language="en", search_queries=["test"])
        )),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.query", AsyncMock(side_effect=mock_query)),
        patch("app.services.opensearch_service.OpenSearchService.search", AsyncMock(return_value=[])),
    ):
        agent = RetrievalAgent()
        await agent.run(
            RetrievalInput(
                organization_id="org-abc",
                user_id="user-1",
                query="test",
            )
        )

    assert captured_ns == ["org_org-abc"]


def test_rerank_by_hybrid_evidence_prioritizes_query_supported_chunk():
    agent = RetrievalAgent()
    chunks = [
        RetrievedChunk(
            vector_id="report_0",
            document_id="report-6",
            chunk_index=0,
            content="WeatherTrip user guide and onboarding flow.",
            score=0.031,
            file_name="Report6_Software_User_Guides.docx",
            file_type="docx",
            access_scope="organization",
            metadata={
                "final_score": 0.031,
                "score_vector": 0.84,
                "score_lexical": 0.0,
            },
        ),
        RetrievedChunk(
            vector_id="privacy_2",
            document_id="privacy-doc",
            chunk_index=2,
            content="Privacy Policy about data collection, retention, and deletion rights.",
            score=0.031,
            file_name="privacy-policy.pdf",
            file_type="pdf",
            access_scope="organization",
            metadata={
                "final_score": 0.031,
                "score_vector": 0.83,
                "score_lexical": 3.6,
            },
        ),
    ]

    reranked = agent._rerank_by_hybrid_evidence(
        RetrievalInput(
            organization_id="org-1",
            user_id="user-1",
            query="privacy policy quyen xoa du lieu",
            extra={"retrieval_search_queries": ["privacy policy quyen xoa du lieu"]},
        ),
        chunks,
    )

    assert reranked[0].document_id == "privacy-doc"
    assert reranked[0].metadata["evidence_rank_score"] >= reranked[1].metadata["evidence_rank_score"]


@pytest.mark.asyncio
async def test_retrieval_agent_uses_query_expansion_for_cross_language_search():
    captured_queries = []
    captured_vectors = []

    async def mock_vector_query(vector, namespace, filter, top_k):
        captured_vectors.append(vector)
        if vector == [0.2] * 1536:
            return [
                ScoredVector(
                    id="doc-en_0",
                    score=0.91,
                    metadata={
                        "document_id": "doc-en",
                        "chunk_index": 0,
                        "content": "Leave policy for full-time employees.",
                        "file_name": "leave-policy.pdf",
                        "file_type": "pdf",
                        "access_scope": "organization",
                        "token_count": 40,
                    },
                )
            ]
        return []

    async def mock_lexical_search(**kwargs):
        captured_queries.append(kwargs["query"])
        if kwargs["query"] == "leave policy for employees":
            return []
        return []

    with (
        patch("app.agents.retrieval_agent.QueryExpansionService.expand", AsyncMock(
            return_value=QueryExpansionResult(
                detected_language="vi",
                search_queries=["chính sách nghỉ phép cho nhân viên", "leave policy for employees"],
            )
        )),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536, [0.2] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.query", AsyncMock(side_effect=mock_vector_query)),
        patch("app.services.opensearch_service.OpenSearchService.search", AsyncMock(side_effect=mock_lexical_search)),
        patch(
            "app.agents.retrieval_agent.BeCoreClient.batch_access_check",
            AsyncMock(return_value={"allowed": ["doc-en"], "denied": []}),
        ),
    ):
        agent = RetrievalAgent()
        result = await agent.run(
            RetrievalInput(
                organization_id="org-123",
                user_id="user-1",
                query="chính sách nghỉ phép cho nhân viên",
                top_k=6,
            )
        )

    assert captured_queries == [
        "chính sách nghỉ phép cho nhân viên",
        "leave policy for employees",
    ]
    assert captured_vectors == [[0.1] * 1536, [0.2] * 1536]
    assert result.success is True
    assert len(result.chunks) == 1
    assert result.chunks[0].document_id == "doc-en"
