from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class CitationSchema(BaseModel):
    document_id: str
    document_name: str
    chunk_text: str
    score: float
    chunk_index: Optional[int] = None
    chunk_count: int = 1
    page_number: Optional[int] = None


class ImageAttachmentSchema(BaseModel):
    """Ảnh đính kèm trong một tin nhắn của user."""
    url: str = Field(..., description="URL ảnh trên ImageKit (sau khi upload)")
    mime_type: str = Field(default="image/jpeg", description="MIME type của ảnh")
    original_name: Optional[str] = Field(None, description="Tên file gốc")
    ocr_text: Optional[str] = Field(
        None,
        description="Text trích xuất từ ảnh bằng Gemini Vision (cached để dùng trong context)",
    )


class MessageSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    organization_id: str
    user_id: str
    role: Literal["user", "assistant"]
    content: str
    # Ảnh đính kèm (chỉ role=user mới có, tối đa 1 ảnh/message)
    image: Optional[ImageAttachmentSchema] = None
    mode: Optional[str] = None  # auto | rag | web | hybrid | tool | chitchat
    intent: Optional[str] = None
    citations: list[CitationSchema] = []
    web_sources: list[dict] = []  # for web search mode
    model_name: Optional[str] = None
    model_used: Optional[str] = None
    model_config_id: Optional[str] = None
    tokens_used: Optional[int] = None
    documents_retrieved: Optional[int] = None
    response_time_ms: Optional[int] = None
    tool_calls: list[dict] = Field(default_factory=list)
    pending_action_id: Optional[str] = None
    metadata: dict = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
