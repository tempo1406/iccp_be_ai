import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def mock_settings(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("PINECONE_API_KEY", "test-pinecone-key")
    monkeypatch.setenv("INTERNAL_API_KEY", "test-internal-key")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret")
    from app.core.config import get_settings
    get_settings.cache_clear()


@pytest.fixture
def mock_pinecone_service():
    with patch("app.services.pinecone_service.PineconeService") as mock:
        mock.upsert = AsyncMock(return_value=None)
        mock.query = AsyncMock(return_value=[])
        mock.delete_by_filter = AsyncMock(return_value=None)
        mock.initialize = AsyncMock(return_value=None)
        yield mock


@pytest.fixture
def mock_embedding_service():
    with patch("app.services.embedding_service.EmbeddingService") as mock:
        mock.embed_one = AsyncMock(return_value=[0.1] * 1536)
        mock.embed_batch = AsyncMock(return_value=[[0.1] * 1536])
        yield mock


@pytest.fixture
def mock_llm_service():
    with patch("app.services.llm_service.LLMService") as mock:
        from app.services.llm_service import LLMResponse
        mock.ainvoke = AsyncMock(
            return_value=LLMResponse(
                content="Đây là câu trả lời test.",
                tokens_used=50,
                model="gpt-4o-mini",
            )
        )
        yield mock


@pytest.fixture
def mock_be_core_client():
    with patch("app.clients.be_core_client.BeCoreClient") as mock:
        mock.initialize = AsyncMock(return_value=None)
        mock.update_document_status = AsyncMock(return_value=None)
        mock.save_document_chunks = AsyncMock(return_value=None)
        mock.save_message = AsyncMock(return_value="test-message-id")
        mock.save_citations = AsyncMock(return_value=None)
        mock.get_conversation_messages = AsyncMock(return_value=[])
        mock.record_analytics = AsyncMock(return_value=None)
        yield mock


@pytest_asyncio.fixture
async def test_client():
    from app.main import app
    with patch("app.services.pinecone_service.PineconeService.initialize", AsyncMock()):
        with patch("app.clients.be_core_client.BeCoreClient.initialize", AsyncMock()):
            with patch("app.clients.be_core_client.BeCoreClient.close", AsyncMock()):
                async with AsyncClient(
                    transport=ASGITransport(app=app),
                    base_url="http://test",
                ) as client:
                    yield client


@pytest.fixture
def valid_jwt_token():
    from jose import jwt
    from app.core.config import settings
    payload = {
        "sub": "test-user-id",
        "organizationId": "test-org-id",
        "email": "test@example.com",
        "roles": [],
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
