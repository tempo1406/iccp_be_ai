import pytest

from app.services.chunking_service import ChunkingService, Chunk
from app.core.exceptions import ChunkingException


def test_chunk_basic_text():
    text = "Đây là đoạn văn bản thử nghiệm. " * 20
    chunks = ChunkingService.chunk_text(text)
    assert len(chunks) >= 1
    for chunk in chunks:
        assert isinstance(chunk, Chunk)
        assert chunk.content.strip() != ""
        assert chunk.token_count > 0


def test_chunk_preserves_order():
    text = "\n\n".join([f"Đoạn văn số {i}." for i in range(10)])
    chunks = ChunkingService.chunk_text(text)
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


def test_chunk_empty_text_raises():
    with pytest.raises(ChunkingException):
        ChunkingService.chunk_text("   ")


def test_chunk_metadata_propagated():
    text = "Test content for metadata check."
    base_meta = {"document_id": "doc-1", "organization_id": "org-1"}
    chunks = ChunkingService.chunk_text(text, base_meta)
    for chunk in chunks:
        assert chunk.metadata["document_id"] == "doc-1"
        assert chunk.metadata["organization_id"] == "org-1"


def test_chunk_long_document():
    """Should handle a 10,000 char document without error."""
    text = "Đây là nội dung tài liệu. " * 400
    chunks = ChunkingService.chunk_text(text)
    assert len(chunks) > 1
