import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_embed_one_returns_vector():
    mock_response = {"embedding": [[0.1] * 1536]}

    with (
        patch("app.services.embedding_service._get_redis") as mock_redis_fn,
        patch("app.services.embedding_service.genai.embed_content_async", new_callable=AsyncMock) as mock_gemini,
        patch("app.services.embedding_service.EmbeddingService._ensure_configured"),
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=None)
        mock_redis_fn.return_value = mock_redis

        mock_gemini.return_value = mock_response

        from app.services.embedding_service import EmbeddingService
        result = await EmbeddingService.embed_one("Test text")

    assert isinstance(result, list)
    assert len(result) == 1536


@pytest.mark.asyncio
async def test_embed_batch_returns_correct_count():
    texts = ["text 1", "text 2", "text 3"]
    mock_response = {"embedding": [[0.1] * 1536 for _ in texts]}

    with (
        patch("app.services.embedding_service._get_redis") as mock_redis_fn,
        patch("app.services.embedding_service.genai.embed_content_async", new_callable=AsyncMock) as mock_gemini,
        patch("app.services.embedding_service.EmbeddingService._ensure_configured"),
    ):
        mock_redis = AsyncMock()
        mock_redis.get = AsyncMock(return_value=None)
        mock_redis.setex = AsyncMock(return_value=None)
        mock_redis_fn.return_value = mock_redis

        mock_gemini.return_value = mock_response

        from app.services.embedding_service import EmbeddingService
        results = await EmbeddingService.embed_batch(texts)

    assert len(results) == 3
    assert all(len(v) == 1536 for v in results)


@pytest.mark.asyncio
async def test_embed_batch_empty_returns_empty():
    from app.services.embedding_service import EmbeddingService
    result = await EmbeddingService.embed_batch([])
    assert result == []
