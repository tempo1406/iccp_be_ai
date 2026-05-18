from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BatchAccessCheckRequest:
    user_id: str
    organization_id: str
    document_ids: list[str] = field(default_factory=list)
    role_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)
