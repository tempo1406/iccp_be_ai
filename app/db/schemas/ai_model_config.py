from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.schemas.ai_model_config import (
    AIModelProvider,
    AIModelPurpose,
    AIModelTransport,
)


class AIModelConfigSchema(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    provider: AIModelProvider
    transport: AIModelTransport
    purpose_codes: list[AIModelPurpose]
    model_name: str
    model_display_name: str
    api_key: str
    api_key_masked: str
    api_base_url: Optional[str] = None
    sdk_name: Optional[str] = None
    description: Optional[str] = None
    is_enabled: bool = True
    applies_to_all_plans: bool = True
    allowed_subscription_plan_codes: list[str] = Field(default_factory=list)
    priority: int = 100
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None

