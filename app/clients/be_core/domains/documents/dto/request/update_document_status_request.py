from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class UpdateDocumentStatusRequest:
    document_id: str
    status: str
    error_msg: Optional[str] = None
