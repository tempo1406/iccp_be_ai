from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.core.exceptions import TenantIsolationException
from app.prompts.intent_router import INTENT_ROUTER_SYSTEM_PROMPT, INTENT_ROUTER_USER_TEMPLATE
from app.schemas.ai_model_config import AIModelPurpose
from app.services.llm_service import ChatMessage, LLMService

log = structlog.get_logger(__name__)

Intent = Literal["DOCUMENT_QUERY", "TASK_QUERY", "TOOL_QUERY", "CHITCHAT"]

_VALID_INTENTS: set[str] = {"DOCUMENT_QUERY", "TASK_QUERY", "TOOL_QUERY", "CHITCHAT"}
_DEFAULT_INTENT: Intent = "DOCUMENT_QUERY"

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        from app.core.config import settings
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _intent_cache_key(
    message: str,
    *,
    organization_id: str,
    mode: str,
    toolset: str,
    context_scope: str,
    context_id: str | None,
) -> str:
    raw = "|".join([
        organization_id,
        mode,
        toolset,
        context_scope,
        context_id or "",
        message,
    ])
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"intent:v3:{digest}"


@dataclass
class RouterInput(AgentInput):
    message: str = ""
    context_scope: str = "organization"
    context_id: str | None = None
    mode: str = "general"
    toolset: str = "none"


@dataclass
class RouterOutput(AgentOutput):
    intent: Intent = "DOCUMENT_QUERY"
    context_scope: str = "organization"
    context_id: str | None = None


class RouterAgent(BaseAgent):
    """
    Classifies user intent and determines routing:
    - DOCUMENT_QUERY → RetrievalAgent + ChatAgent (RAG)
    - TASK_QUERY     → RetrievalAgent + ChatAgent (project-scoped)
    - TOOL_QUERY     → ToolPlanningAgent + ToolExecutor + ChatAgent
    - CHITCHAT       → ChatAgent direct (no retrieval)
    """

    async def run(self, input: AgentInput) -> RouterOutput:
        assert isinstance(input, RouterInput), "Expected RouterInput"

        if not input.organization_id:
            raise TenantIsolationException("organization_id required in RouterInput")

        intent = await self._classify_intent(
            input.message,
            organization_id=input.organization_id,
            mode=input.mode,
            toolset=input.toolset,
            context_scope=input.context_scope,
            context_id=input.context_id,
            trace_id=input.trace_id,
        )

        log.info(
            "router_agent.classified",
            intent=intent,
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            context_scope=input.context_scope,
            context_id=input.context_id,
            message_preview=input.message[:60],
        )

        # TASK_QUERY uses project scope if context_id is provided
        effective_scope = input.context_scope
        effective_context_id = input.context_id

        if intent == "TASK_QUERY" and not input.context_id:
            # No project context but asking about tasks → treat as tool query
            # ToolPlanningAgent will resolve project automatically
            intent = "TOOL_QUERY"
            log.debug(
                "router_agent.upgraded_task_to_tool_query",
                trace_id=input.trace_id,
                reason="no_project_context",
            )

        # TOOL_QUERY is allowed even without project context
        # ToolPlanningAgent will handle project resolution

        return RouterOutput(
            success=True,
            intent=intent,
            context_scope=effective_scope,
            context_id=effective_context_id,
        )

    async def _classify_intent(
        self,
        message: str,
        *,
        organization_id: str,
        mode: str,
        toolset: str,
        context_scope: str,
        context_id: str | None,
        trace_id: str,
    ) -> Intent:
        """
        Classify intent with Redis cache (TTL = INTENT_CACHE_TTL seconds).
        Cache is scoped by tenant and runtime context because routing can differ
        for the same text across mode/scope/tool choices.
        Falls back to DOCUMENT_QUERY on any error.
        """
        from app.core.config import settings

        redis = _get_redis()
        cache_key = _intent_cache_key(
            message,
            organization_id=organization_id,
            mode=mode,
            toolset=toolset,
            context_scope=context_scope,
            context_id=context_id,
        )

        # ── Cache hit ──────────────────────────────────────────────────────
        cached = await redis.get(cache_key)
        if cached and cached in _VALID_INTENTS:
            log.debug(
                "router_agent.intent_cache_hit",
                trace_id=trace_id,
                intent=cached,
            )
            return cached  # type: ignore[return-value]

        # ── LLM classification ─────────────────────────────────────────────
        try:
            messages = [
                ChatMessage(role="system", content=INTENT_ROUTER_SYSTEM_PROMPT),
                ChatMessage(
                    role="human",
                    content=INTENT_ROUTER_USER_TEMPLATE.format(message=message),
                ),
            ]
            response = await LLMService.ainvoke(
                messages,
                organization_id=organization_id,
                purpose=AIModelPurpose.INTENT_ROUTER,
            )
            raw = response.content.strip().upper()
            log.info(
                "router_agent.intent_classified",
                trace_id=trace_id,
                organization_id=organization_id,
                raw_intent=raw,
                cached=False,
            )

            intent: Intent = raw if raw in _VALID_INTENTS else _DEFAULT_INTENT  # type: ignore[assignment]
            if raw not in _VALID_INTENTS:
                log.warning("router_agent.unknown_intent", trace_id=trace_id, raw=raw)

            # ── Cache result ───────────────────────────────────────────────
            await redis.setex(cache_key, settings.INTENT_CACHE_TTL, intent)
            return intent

        except Exception as exc:
            log.error(
                "router_agent.classification_failed",
                trace_id=trace_id,
                organization_id=organization_id,
                error=str(exc),
            )
            return _DEFAULT_INTENT
