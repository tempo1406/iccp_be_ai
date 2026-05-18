from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DocumentInfoResponse:
    document_id: str
    organization_id: str
    file_path: str
    status: str
    access_scope: str
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    folder_id: Optional[str] = None
    folder_path_ids: list[str] = field(default_factory=list)
    project_id: Optional[str] = None
    category_id: Optional[str] = None
    uploaded_by: Optional[str] = None
    mime_type: Optional[str] = None
    title: Optional[str] = None
    is_active: bool = True
    version: int = 1
