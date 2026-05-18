from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class OrgQuotaSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    organization_id: str  # unique index
    monthly_message_limit: int = 1000
    monthly_messages_used: int = 0
    token_limit: int = 10000000
    tokens_used: int = 0
    monthly_ingestion_limit: int = 100
    monthly_ingestions_used: int = 0
    reset_at: datetime  # when monthly quota resets
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserQuotaSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: str
    organization_id: str
    daily_message_limit: int = 100
    daily_messages_used: int = 0
    daily_token_limit: int = 100000
    daily_tokens_used: int = 0
    reset_at: datetime  # when daily quota resets (next day)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)