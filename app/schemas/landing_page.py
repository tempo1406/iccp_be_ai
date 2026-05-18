from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class OrgContext(BaseModel):
    name: str
    slug: str
    description: Optional[str] = None
    industry: Optional[str] = None
    logo_url: Optional[str] = None
    primary_color: Optional[str] = None


class LandingPageConversationMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class GenerateLandingPageRequest(BaseModel):
    org_context: OrgContext
    conversation: list[LandingPageConversationMessage] = []
    mode: Optional[Literal["generate", "modify"]] = None
    user_prompt: str
    current_html: Optional[str] = None
    custom_api_key: Optional[str] = None


class LandingPageChunk(BaseModel):
    type: str  # "chunk" | "done" | "error"
    content: Optional[str] = None
    message: Optional[str] = None
    tokens_used: Optional[int] = None
