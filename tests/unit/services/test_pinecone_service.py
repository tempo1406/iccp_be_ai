import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.exceptions import VectorStoreException
from app.services.pinecone_service import PineconeService, PineconeVector


@pytest.mark.asyncio
async def test_upsert_batches_correctly():
    """Should batch upserts for > 100 vectors."""
    call_counts = []

    def mock_upsert_sync(vectors, namespace):
        call_counts.append(len(vectors))

    mock_index = MagicMock()
    mock_index.upsert = mock_upsert_sync
    PineconeService._index = mock_index

    vectors = [
        PineconeVector(id=f"v{i}", values=[0.1] * 1536, metadata={})
        for i in range(250)
    ]
    await PineconeService.upsert(vectors=vectors, namespace="org_test")

    assert len(call_counts) == 3  # ceil(250/100) = 3
    assert sum(call_counts) == 250


@pytest.mark.asyncio
async def test_upsert_empty_list_does_nothing():
    mock_index = MagicMock()
    PineconeService._index = mock_index

    await PineconeService.upsert(vectors=[], namespace="org_test")
    mock_index.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_query_returns_scored_vectors():
    mock_match = MagicMock()
    mock_match.id = "doc-1_0"
    mock_match.score = 0.95
    mock_match.metadata = {"content": "Test content"}

    mock_result = MagicMock()
    mock_result.matches = [mock_match]

    mock_index = MagicMock()
    mock_index.query = MagicMock(return_value=mock_result)
    PineconeService._index = mock_index

    results = await PineconeService.query(
        vector=[0.1] * 1536,
        namespace="org_test",
        filter={"organization_id": {"$eq": "org-1"}},
        top_k=5,
    )

    assert len(results) == 1
    assert results[0].id == "doc-1_0"
    assert results[0].score == 0.95


@pytest.mark.asyncio
async def test_uninitialized_raises():
    PineconeService._index = None
    with pytest.raises(VectorStoreException, match="not initialized"):
        await PineconeService.query(vector=[0.1] * 1536, namespace="org_test")
