from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent

log = structlog.get_logger(__name__)


@dataclass
class AnalyticsInput(AgentInput):
    conversation_id: str = ""
    message_id: str = ""
    query_text: str = ""
    response_time_ms: int = 0
    tokens_used: int = 0
    documents_retrieved: int = 0
    feedback_rating: Optional[int] = None


class AnalyticsAgent(BaseAgent):
    """
    Records chat analytics. Logs structured events.
    Failures are logged but never raise — analytics must not block chat flow.
    """

    async def run(self, input: AgentInput) -> AgentOutput:
        assert isinstance(input, AnalyticsInput), "Expected AnalyticsInput"

        try:
            log.info(
                "analytics.chat_event",
                organization_id=input.organization_id,
                user_id=input.user_id,
                conversation_id=input.conversation_id,
                message_id=input.message_id,
                query_preview=input.query_text[:80],
                response_time_ms=input.response_time_ms,
                tokens_used=input.tokens_used,
                documents_retrieved=input.documents_retrieved,
                feedback_rating=input.feedback_rating,
            )
            return AgentOutput(success=True)

        except Exception as exc:
            log.warning("analytics_agent.failed", error=str(exc))
            return AgentOutput(success=False, error=str(exc))
