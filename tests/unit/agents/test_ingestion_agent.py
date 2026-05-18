import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.ingestion_agent import IngestionAgent, IngestionInput, IngestionOutput
from app.core.exceptions import IngestionException
from app.services.chunking_service import Chunk


@pytest.mark.asyncio
async def test_ingestion_agent_success(tmp_path):
    """IngestionAgent should parse, chunk, embed, and upsert successfully."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("Đây là nội dung tài liệu thử nghiệm để kiểm tra chunking.")

    with (
        patch("app.services.file_parser_service.FileParserService.parse", AsyncMock(
            return_value="Đây là nội dung tài liệu thử nghiệm."
        )),
        patch("app.services.chunking_service.ChunkingService.chunk_text", return_value=[
            Chunk(chunk_index=0, content="Đây là nội dung.", token_count=10)
        ]),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.1] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.upsert", AsyncMock(return_value=None)),
    ):
        agent = IngestionAgent()
        result = await agent.run(
            IngestionInput(
                organization_id="org-123",
                user_id="user-456",
                document_id="doc-789",
                file_path=str(test_file),
                file_name="test.txt",
                file_type="txt",
                access_scope="organization",
            )
        )

    assert isinstance(result, IngestionOutput)
    assert result.success is True
    assert result.chunks_count == 1
    assert len(result.chunks) == 1


@pytest.mark.asyncio
async def test_ingestion_agent_empty_file_raises():
    """IngestionAgent should raise IngestionException on empty file content."""
    with (
        patch("app.services.file_parser_service.FileParserService.parse", AsyncMock(
            return_value="   "
        )),
    ):
        agent = IngestionAgent()
        with pytest.raises(IngestionException, match="empty content"):
            await agent.run(
                IngestionInput(
                    organization_id="org-123",
                    user_id="user-456",
                    document_id="doc-789",
                    file_path="/fake/path.txt",
                    file_name="empty.txt",
                    file_type="txt",
                )
            )


@pytest.mark.asyncio
async def test_ingestion_agent_namespace_uses_org_id():
    """Pinecone upsert must use org_{organization_id} namespace."""
    captured_namespace = []

    async def mock_upsert(vectors, namespace):
        captured_namespace.append(namespace)

    with (
        patch("app.services.file_parser_service.FileParserService.parse", AsyncMock(
            return_value="Some content here."
        )),
        patch("app.services.chunking_service.ChunkingService.chunk_text", return_value=[
            Chunk(chunk_index=0, content="Some content.", token_count=5)
        ]),
        patch("app.services.embedding_service.EmbeddingService.embed_batch", AsyncMock(
            return_value=[[0.2] * 1536]
        )),
        patch("app.services.pinecone_service.PineconeService.upsert", AsyncMock(side_effect=mock_upsert)),
    ):
        agent = IngestionAgent()
        await agent.run(
            IngestionInput(
                organization_id="test-org-999",
                user_id="user-1",
                document_id="doc-1",
                file_path="/fake/file.txt",
                file_name="file.txt",
                file_type="txt",
            )
        )

    assert captured_namespace == ["org_test-org-999"]
