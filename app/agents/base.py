from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class AgentInput:
    """Base input shared by all agents. Every agent receives tenant context."""
    organization_id: str
    user_id: str
    trace_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentOutput:
    """Base output returned by all agents."""
    success: bool
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


class BaseAgent(ABC):
    """Abstract base class for all ICCP AI agents."""

    @abstractmethod
    async def run(self, input: AgentInput) -> AgentOutput:
        """Execute the agent logic. Must be implemented by subclasses."""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__
