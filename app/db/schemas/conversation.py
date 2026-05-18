from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.schemas.chat_runtime import (
    ChatMode,
    ChatToolset,
    normalize_chat_mode,
    normalize_chat_toolset,
)


class ConversationSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str
    user_id: str
    title: Optional[str] = None
    mode: ChatMode = ChatMode.GENERAL
    toolset: ChatToolset = ChatToolset.NONE
    status: str = "active"
    metadata: dict = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        return normalize_chat_mode(value if isinstance(value, (str, ChatMode)) else None)

    @field_validator("toolset", mode="before")
    @classmethod
    def normalize_toolset(cls, value: object) -> object:
        return normalize_chat_toolset(
            value if isinstance(value, (str, ChatToolset)) else None
        )
