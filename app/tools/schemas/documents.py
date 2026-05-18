from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ListDocumentsInput(BaseModel):
    """List documents in the organization."""

    folder_id: Optional[str] = Field(None, description="Filter by folder ID")
    category_id: Optional[str] = Field(None, description="Filter by category ID")
    project_id: Optional[str] = Field(None, description="Filter by project ID")
    page: Optional[int] = Field(1, description="Page number")
    limit: Optional[int] = Field(20, description="Items per page")


class GetDocumentTreeInput(BaseModel):
    """Get the full folder/document tree."""

    include_deleted: Optional[bool] = Field(False, description="Include deleted items")
