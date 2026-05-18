from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GetOrgProfileInput(BaseModel):
    """Get organization profile."""

    organization_id: Optional[str] = Field(None, description="Organization ID (defaults to current)")


class ListOrgMembersInput(BaseModel):
    """List members in the organization."""

    page: Optional[int] = Field(1, description="Page number")
    limit: Optional[int] = Field(20, description="Items per page")
    search: Optional[str] = Field(None, description="Search query")
