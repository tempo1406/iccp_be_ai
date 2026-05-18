from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog
from langchain.text_splitter import RecursiveCharacterTextSplitter

from app.core.config import settings
from app.core.exceptions import ChunkingException

log = structlog.get_logger(__name__)


@dataclass
class Chunk:
    chunk_index: int
    content: str
    token_count: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for Vietnamese/English mixed."""
    return max(1, len(text) // 4)


def _normalize_text(text: str) -> str:
    """Normalize whitespace and remove control characters."""
    text = re.sub(r"\r\n", "\n", text)
    text = re.sub(r"\r", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


class ChunkingService:

    @classmethod
    def chunk_text(cls, text: str, base_metadata: dict[str, Any] | None = None) -> list[Chunk]:
        """
        Split text into chunks using RecursiveCharacterTextSplitter.
        Optimized for Vietnamese and English mixed content.
        """
        if not text or not text.strip():
            raise ChunkingException("Cannot chunk empty text")

        text = _normalize_text(text)
        base_metadata = base_metadata or {}

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE * 4,  # convert tokens → chars estimate
            chunk_overlap=settings.CHUNK_OVERLAP * 4,
            length_function=len,
            separators=[
                "\n\n",   # Ưu tiên tách tại blank line (giữa bảng và đoạn văn)
                "\n",     # Tách tại dòng mới (mỗi data row của bảng đã flatten là 1 dòng)
                "。",
                ".",
                "！",
                "!",
                "？",
                "?",
                "；",
                ";",
                " ",
                "",
            ],
            is_separator_regex=False,
        )

        raw_chunks = splitter.split_text(text)

        if not raw_chunks:
            raise ChunkingException("Text splitter produced no chunks")

        chunks = [
            Chunk(
                chunk_index=i,
                content=raw_chunk.strip(),
                token_count=_estimate_tokens(raw_chunk),
                metadata={**base_metadata, "chunk_index": i},
            )
            for i, raw_chunk in enumerate(raw_chunks)
            if raw_chunk.strip()
        ]

        log.debug(
            "chunking.complete",
            text_length=len(text),
            chunks_count=len(chunks),
            avg_chunk_size=sum(c.token_count for c in chunks) // max(1, len(chunks)),
        )
        return chunks
