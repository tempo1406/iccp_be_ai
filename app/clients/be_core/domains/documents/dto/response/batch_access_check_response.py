from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class BatchAccessCheckResponse:
    allowed: list[str] = field(default_factory=list)
    denied: list[str] = field(default_factory=list)
