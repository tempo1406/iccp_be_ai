from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class GetDocumentInfoRequest:
    document_id: str
    bearer_token: Optional[str] = None
