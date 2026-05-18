from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ListProjectsInput(BaseModel):
    """List projects the user has access to."""

    page: Optional[int] = Field(1, description="Page number")
    limit: Optional[int] = Field(20, description="Items per page")


class GetProjectDetailInput(BaseModel):
    """Get project details by ID or slug."""

    project_id: str = Field(..., description="Project ID or slug")
