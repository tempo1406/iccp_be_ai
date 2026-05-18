from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AIModelProvider(str, Enum):
    GEMINI = "gemini"
    BEEKNOEE = "beeknoee"


class AIModelTransport(str, Enum):
    SDK = "sdk"
    HTTP_API = "http_api"


class AIModelPurpose(str, Enum):
    CHAT_COMPLETION = "chat_completion"
    INTENT_ROUTER = "intent_router"
    SUGGESTION = "suggestion"
    CONTENT_MODERATION = "content_moderation"
    EMBEDDING = "embedding"
    LANDING_PAGE_GENERATION = "landing_page_generation"
    LANDING_PAGE_STATUS = "landing_page_status"
    TOOL_PLANNING = "tool_planning"


def _normalize_plan_codes(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for value in values:
        code = value.strip().lower()
        if code and code not in seen:
            seen.add(code)
            normalized.append(code)

    return normalized


class AIModelConfigCreateRequest(BaseModel):
    provider: AIModelProvider
    transport: AIModelTransport
    purpose_codes: list[AIModelPurpose] = Field(min_length=1)
    model_name: str = Field(min_length=1, max_length=255)
    model_display_name: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=1)
    api_base_url: Optional[str] = Field(default=None, max_length=500)
    sdk_name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_enabled: bool = True
    applies_to_all_plans: bool = True
    allowed_subscription_plan_codes: list[str] = Field(default_factory=list)
    priority: int = Field(default=100, ge=0, le=10000)

    @field_validator("model_name", "model_display_name", "api_key", mode="before")
    @classmethod
    def _strip_required_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_base_url", "sdk_name", "description", mode="before")
    @classmethod
    def _strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("allowed_subscription_plan_codes", mode="before")
    @classmethod
    def _normalize_allowed_plan_codes(cls, value: list[str] | None) -> list[str]:
        return _normalize_plan_codes(value or [])

    @model_validator(mode="after")
    def _validate_model_config(self) -> "AIModelConfigCreateRequest":
        if self.provider == AIModelProvider.BEEKNOEE and self.transport != AIModelTransport.HTTP_API:
            raise ValueError("beeknoee provider only supports http_api transport")

        if self.transport == AIModelTransport.SDK and not self.sdk_name:
            if self.provider == AIModelProvider.GEMINI:
                self.sdk_name = "langchain_google_genai"

        if not self.applies_to_all_plans and not self.allowed_subscription_plan_codes:
            raise ValueError("allowed_subscription_plan_codes is required when applies_to_all_plans is false")

        return self


class AIModelConfigUpdateRequest(BaseModel):
    provider: Optional[AIModelProvider] = None
    transport: Optional[AIModelTransport] = None
    purpose_codes: Optional[list[AIModelPurpose]] = None
    model_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    model_display_name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    api_key: Optional[str] = Field(default=None, min_length=1)
    api_base_url: Optional[str] = Field(default=None, max_length=500)
    sdk_name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    is_enabled: Optional[bool] = None
    applies_to_all_plans: Optional[bool] = None
    allowed_subscription_plan_codes: Optional[list[str]] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)

    @field_validator("model_name", "model_display_name", "api_key", mode="before")
    @classmethod
    def _strip_update_required_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip()

    @field_validator("api_base_url", "sdk_name", "description", mode="before")
    @classmethod
    def _strip_update_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("allowed_subscription_plan_codes", mode="before")
    @classmethod
    def _normalize_update_plan_codes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _normalize_plan_codes(value)


class AIModelConfigResponse(BaseModel):
    id: str
    provider: AIModelProvider
    transport: AIModelTransport
    purpose_codes: list[AIModelPurpose]
    model_name: str
    model_display_name: str
    api_key_masked: str
    api_base_url: Optional[str] = None
    sdk_name: Optional[str] = None
    description: Optional[str] = None
    is_enabled: bool
    applies_to_all_plans: bool
    allowed_subscription_plan_codes: list[str] = Field(default_factory=list)
    priority: int
    is_deleted: bool = False
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None


class AIModelConfigListResponse(BaseModel):
    items: list[AIModelConfigResponse]
    total: int


class AIModelOptionResponse(BaseModel):
    id: str
    name: str


class AIModelOptionListResponse(BaseModel):
    items: list[AIModelOptionResponse]
    total: int

