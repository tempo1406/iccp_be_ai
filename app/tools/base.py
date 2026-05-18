from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ToolContext:
    """Context passed to every tool execution."""

    organization_id: str
    user_id: str
    bearer_token: str | None = None
    trace_id: str = ""


@dataclass
class ToolResult:
    """Standardized result from tool execution."""

    success: bool
    tool: str
    data: dict[str, Any] | None = None
    error: str = ""
