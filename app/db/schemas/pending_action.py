from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PendingActionSchema(BaseModel):
    """A pending tool action awaiting user confirmation."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    conversation_id: str
    user_id: str
    organization_id: str
    tool_name: str
    params: dict
    preview: str
    status: Literal["pending", "confirmed", "cancelled", "expired", "executed", "failed"] = "pending"
    error: Optional[str] = None
    confirmed_at: Optional[datetime] = None
    executed_at: Optional[datetime] = None
    failed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime = Field(default_factory=lambda: datetime.utcnow() + timedelta(minutes=10))

    def is_expired(self) -> bool:
        return datetime.utcnow() > self.expires_at

    def is_pending(self) -> bool:
        return self.status == "pending" and not self.is_expired()
