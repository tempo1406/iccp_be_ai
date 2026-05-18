from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.services.web_search_service import WebSearchService

log = structlog.get_logger(__name__)

_MAX_SNIPPET_CHARS = 500


@dataclass
class WebSearchInput(AgentInput):
    query: str = ""
    max_results: int = 5


@dataclass
class WebSearchOutput(AgentOutput):
    context: str = ""  # Formatted context string for LLM
    sources: list[dict[str, Any]] = field(default_factory=list)  # Raw results for citations


class WebSearchAgent(BaseAgent):
    """
    Searches the web using DuckDuckGo and formats results as LLM context.
    Used for 'web' and 'hybrid' chat modes.
    """

    async def run(self, input: AgentInput) -> WebSearchOutput:
        assert isinstance(input, WebSearchInput), "Expected WebSearchInput"

        log.debug(
            "web_search_agent.started",
            trace_id=input.trace_id,
            query=input.query[:60],
            organization_id=input.organization_id,
            max_results=input.max_results,
        )

        results = await WebSearchService.search(
            query=input.query,
            max_results=input.max_results,
        )

        if not results:
            log.info(
                "web_search_agent.no_results",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
                query=input.query[:60],
            )
            return WebSearchOutput(
                success=True,
                context="",
                sources=[],
            )

        context = self._format_context(results)

        log.info(
            "web_search_agent.complete",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            results=len(results),
            top_urls=[r.get("url") for r in results[:3]],
        )

        return WebSearchOutput(
            success=True,
            context=context,
            sources=results,
        )

    def _format_context(self, results: list[dict[str, Any]]) -> str:
        """Format search results as context string for LLM."""
        parts: list[str] = []
        for i, r in enumerate(results, start=1):
            title = r.get("title", "")
            url = r.get("url", "")
            snippet = r.get("snippet", "")[:_MAX_SNIPPET_CHARS]
            parts.append(f"[{i}] {title}\nURL: {url}\n{snippet}")
        return "\n\n---\n\n".join(parts)
