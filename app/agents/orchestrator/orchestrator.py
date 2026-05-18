from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, AsyncGenerator, Optional

import structlog

from app.agents.analytics_agent import AnalyticsAgent, AnalyticsInput
from app.agents.citation_utils import CitationPreview, citation_preview_to_dict, collapse_citations
from app.agents.chat_agent import ChatAgent, ChatInput, ChatOutput, CitationInfo
from app.agents.orchestrator.context_builder import OrchestratorContextBuilder
from app.agents.orchestrator.persistence_service import OrchestratorPersistenceService
from app.agents.retrieval_agent import RetrievalAgent, RetrievalInput, RetrievedChunk
from app.agents.router_agent import RouterAgent, RouterInput
from app.agents.suggestion_agent import SuggestionAgent
from app.agents.tool_planning_agent import ToolPlanningAgent, ToolPlanningInput
from app.agents.web_search_agent import WebSearchAgent, WebSearchInput
from app.core.config import settings
from app.db.mongodb import get_database
from app.db.repositories.pending_action_repo import PendingActionRepository
from app.db.schemas.message import CitationSchema
from app.db.schemas.pending_action import PendingActionSchema
from app.schemas.ai_model_config import AIModelPurpose
from app.schemas.chat_runtime import (
    normalize_assistant_config,
    normalize_chat_mode,
    normalize_chat_toolset,
    resolve_chat_mode,
)
from app.services.ai_model_config_service import AIModelConfigService
from app.services.content_policy_service import ContentPolicyService
from app.services.quota_service import QuotaService
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import get_tool_names_for_toolset
from app.tools.safeguard import SafeguardGate

log = structlog.get_logger(__name__)


@dataclass
class OrchestratorInput:
    organization_id: str
    user_id: str
    conversation_id: str
    user_message: str
    mode: str = "general"
    toolset: str = "none"
    assistant_config: dict[str, Any] = field(default_factory=dict)
    model_config_id: Optional[str] = None
    context_scope: str = "organization"
    context_id: Optional[str] = None
    context_options: dict[str, Any] = field(default_factory=dict)
    role_ids: list[str] = field(default_factory=list)
    project_ids: list[str] = field(default_factory=list)
    bearer_token: Optional[str] = None
    trace_id: str = ""
    confirmed_action_id: Optional[str] = None
    # FE-provided bounded history (GENERAL mode fast-path)
    inline_history: Optional[list[dict[str, Any]]] = None

    def __post_init__(self) -> None:
        self.toolset = normalize_chat_toolset(self.toolset)
        self.assistant_config = normalize_assistant_config(
            self.assistant_config,
            self.mode,
            self.toolset,
        )
        if not self.assistant_config["internal_enabled"]:
            self.toolset = "none"
        self.mode = resolve_chat_mode(self.mode, self.toolset, self.assistant_config)
        if not self.trace_id:
            self.trace_id = str(uuid.uuid4())

    @property
    def internal_enabled(self) -> bool:
        return bool(self.assistant_config.get("internal_enabled"))

    @property
    def external_enabled(self) -> bool:
        return bool(self.assistant_config.get("external_enabled"))


@dataclass
class OrchestratorOutput:
    response_text: str
    citations: list[CitationInfo]
    tokens_used: int
    message_id: str
    response_time_ms: int


class AgentOrchestrator:
    """
    Entry point for all chat flows.
    Coordinates: RouterAgent → RetrievalAgent/WebSearchAgent → ChatAgent → AnalyticsAgent

    Modes:
    - general: direct assistant with no forced retrieval/tools
    - auto: internal retrieval + web search when needed
    - rag: Pinecone vector retrieval only
    - web: DuckDuckGo web search only
    - chitchat (auto-detected by router): direct LLM
    """

    def __init__(self) -> None:
        self._router = RouterAgent()
        self._retrieval = RetrievalAgent()
        self._web_search = WebSearchAgent()
        self._chat = ChatAgent()
        self._analytics = AnalyticsAgent()
        self._tool_planning = ToolPlanningAgent()
        self._context_builder = OrchestratorContextBuilder()
        self._persistence = OrchestratorPersistenceService()

    async def stream(self, input: OrchestratorInput) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream chat response as SSE-ready dicts.
        Yields: {"type": "token", "content": "..."} and {"type": "done", ...}
        """
        start_ms = time.monotonic()
        structlog.contextvars.bind_contextvars(
            trace_id=input.trace_id,
            organization_id=input.organization_id,
        )

        try:
            log.info(
                "orchestrator.stream_started",
                trace_id=input.trace_id,
                organization_id=input.organization_id,
                user_id=input.user_id,
                conversation_id=input.conversation_id,
                mode=input.mode,
                toolset=input.toolset,
                assistant_config=input.assistant_config,
                context_scope=input.context_scope,
                context_id=input.context_id,
            )

            # ── Confirmed action branch ──────────────────────────────────────
            if input.confirmed_action_id:
                log.info(
                    "orchestrator.confirmed_action_requested",
                    trace_id=input.trace_id,
                    conversation_id=input.conversation_id,
                    confirmed_action_id=input.confirmed_action_id,
                )
                db = get_database()
                repo = PendingActionRepository(db)
                pending = await repo.get_by_id(input.confirmed_action_id)

                if not pending:
                    log.warning(
                        "orchestrator.confirmed_action_not_found",
                        trace_id=input.trace_id,
                        conversation_id=input.conversation_id,
                        confirmed_action_id=input.confirmed_action_id,
                    )
                    yield {"type": "error", "error": "Action not found or expired"}
                    return

                if pending.user_id != input.user_id or pending.conversation_id != input.conversation_id:
                    log.warning(
                        "orchestrator.confirmed_action_access_denied",
                        trace_id=input.trace_id,
                        conversation_id=input.conversation_id,
                        confirmed_action_id=input.confirmed_action_id,
                        pending_user_id=pending.user_id,
                        pending_conversation_id=pending.conversation_id,
                    )
                    yield {"type": "error", "error": "Action access denied"}
                    return

                if not pending.is_pending():
                    status_label = pending.status
                    if pending.status == "pending" and pending.is_expired():
                        await repo.update_status(pending.id, "expired")
                        status_label = "expired"
                    log.warning(
                        "orchestrator.confirmed_action_invalid_status",
                        trace_id=input.trace_id,
                        conversation_id=input.conversation_id,
                        confirmed_action_id=input.confirmed_action_id,
                        pending_status=pending.status,
                    )
                    yield {"type": "error", "error": f"Action is already {status_label}"}
                    return

                if not await repo.transition_status(
                    pending.id,
                    from_status="pending",
                    to_status="confirmed",
                ):
                    yield {"type": "error", "error": "Action was already handled"}
                    return

                history = await self._persistence.load_history(
                    input.conversation_id,
                    settings.MAX_HISTORY_MESSAGES,
                    log,
                )
                async for event in self._handle_confirmed_action(input, pending, history):
                    yield event
                return

            await self._run_guards(input)
            log.info(
                "orchestrator.guards_passed",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
            )

            # ── GENERAL mode short-circuit ───────────────────────────────────
            # Skip router, retrieval, and tool planning entirely — zero extra LLM calls.
            if input.mode == "general" and not input.internal_enabled and not input.external_enabled:
                if input.inline_history is not None:
                    # FE sent bounded history — skip DB round-trip entirely
                    history = input.inline_history
                    log.info(
                        "orchestrator.general_mode_inline_history",
                        trace_id=input.trace_id,
                        conversation_id=input.conversation_id,
                        history_messages=len(history),
                    )
                else:
                    history = await self._persistence.load_history(
                        input.conversation_id,
                        settings.MAX_HISTORY_MESSAGES,
                        log,
                    )
                    log.info(
                        "orchestrator.general_mode_shortcircuit",
                        trace_id=input.trace_id,
                        conversation_id=input.conversation_id,
                        history_messages=len(history),
                    )
                async for event in self._handle_general_mode(input, history, start_ms):
                    yield event
                return

            allowed_tools = (
                get_tool_names_for_toolset(input.toolset) if input.internal_enabled else []
            )

            router_out, history = await self._route_and_load_history(input)
            log.info(
                "orchestrator.router_and_history_ready",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                intent=router_out.intent,
                routed_context_scope=router_out.context_scope,
                routed_context_id=router_out.context_id,
                history_messages=len(history),
            )

            # ── TOOL_QUERY / TASK_QUERY branch ─────────────────────────────
            if router_out.intent in ("TOOL_QUERY", "TASK_QUERY") and allowed_tools:
                async for event in self._handle_tool_flow(input, history, allowed_tools):
                    yield event
                return
            if router_out.intent in ("TOOL_QUERY", "TASK_QUERY") and not allowed_tools:
                log.info(
                    "orchestrator.tool_intent_ignored_without_toolset",
                    trace_id=input.trace_id,
                    conversation_id=input.conversation_id,
                    intent=router_out.intent,
                    toolset=input.toolset,
                )
                router_out.intent = "CHITCHAT"

            chunks, web_sources, is_chitchat = await self._retrieve_context(input, router_out)
            log.info(
                "orchestrator.context_retrieved",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                mode=input.mode,
                is_chitchat=is_chitchat,
                chunks_count=len(chunks),
                web_sources_count=len(web_sources),
            )

            if self._should_abstain_for_missing_evidence(input, chunks, web_sources, is_chitchat):
                async for event in self._stream_insufficient_evidence(input, router_out, start_ms):
                    yield event
                return

            # 4. Determine chat mode and build context
            chat_mode = self._context_builder.determine_chat_mode(
                is_chitchat,
                input.mode,
                chunks,
                web_sources,
            )
            log.info(
                "orchestrator.chat_mode_selected",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                selected_chat_mode=chat_mode,
                chunks_count=len(chunks),
                web_sources_count=len(web_sources),
            )

            web_context = self._context_builder.build_web_context(web_sources) if web_sources else ""

            chat_input = ChatInput(
                organization_id=input.organization_id,
                user_id=input.user_id,
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                user_message=input.user_message,
                retrieved_chunks=chunks,
                history=history,
                mode=chat_mode,
                web_context=web_context,
                model_config_id=input.model_config_id,
            )

            # 5. Save user message to MongoDB
            user_msg_id = await self._persistence.save_message(
                conversation_id=input.conversation_id,
                organization_id=input.organization_id,
                user_id=input.user_id,
                role="user",
                content=input.user_message,
                log=log,
                mode=input.mode,
                intent=router_out.intent,
                model_config_id=input.model_config_id,
            )
            log.info(
                "orchestrator.user_message_saved",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                user_message_id=user_msg_id,
            )

            # 6. Stream chat response — use list+join to avoid O(n²) string concat
            tokens_list: list[str] = []
            stream_tokens_used = 0
            stream_model_used: Optional[str] = None

            def _on_stream_complete(tokens_used: int, model_name: str) -> None:
                nonlocal stream_tokens_used, stream_model_used
                stream_tokens_used = max(stream_tokens_used, int(tokens_used or 0))
                if model_name:
                    stream_model_used = model_name

            async for token in self._chat.stream_response(
                chat_input,
                on_complete=_on_stream_complete,
            ):
                tokens_list.append(token)
                yield {"type": "token", "content": token}

            full_response = "".join(tokens_list)
            # Keep source attribution lightweight: collapse the retrieved chunks by document
            # without running the heavier citation scoring/reranking pass.
            response_citations = collapse_citations(
                [
                    CitationPreview(
                        document_id=chunk.document_id,
                        chunk_index=chunk.chunk_index,
                        file_name=chunk.file_name,
                        relevance_score=round(float(chunk.score), 4),
                        cited_content=chunk.content[:300],
                    )
                    for chunk in chunks
                ]
            )[:4]
            elapsed_ms = int((time.monotonic() - start_ms) * 1000)
            tokens_used = stream_tokens_used or self._estimate_tokens(full_response)

            # 7. Resolve model name for persistence
            resolved_config = await AIModelConfigService.resolve_model_config(
                purpose=AIModelPurpose.CHAT_COMPLETION,
                selected_model_config_id=input.model_config_id,
                organization_id=input.organization_id,
            )
            model_name = resolved_config.model_display_name or resolved_config.model_name
            model_used = stream_model_used or resolved_config.model_name

            # 8. Save assistant message to MongoDB
            citation_schemas = self._persistence.to_citation_schemas(response_citations)

            assistant_msg_id = await self._persistence.save_message(
                conversation_id=input.conversation_id,
                organization_id=input.organization_id,
                user_id=input.user_id,
                role="assistant",
                content=full_response,
                log=log,
                mode=input.mode,
                intent=router_out.intent,
                citations=citation_schemas,
                web_sources=web_sources,
                model_name=model_name,
                model_used=model_used,
                model_config_id=input.model_config_id,
                tokens_used=tokens_used,
                documents_retrieved=len(chunks),
                response_time_ms=elapsed_ms,
            )
            log.info(
                "orchestrator.assistant_message_saved",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                assistant_message_id=assistant_msg_id,
                citations_count=len(response_citations),
                web_sources_count=len(web_sources),
                tokens_used=tokens_used,
                response_time_ms=elapsed_ms,
            )

            # 8. Emit done event with citations + token count
            yield {
                "type": "done",
                "message_id": assistant_msg_id,
                "citations": [citation_preview_to_dict(c) for c in response_citations],
                "web_sources": web_sources[:5],
                "response_time_ms": elapsed_ms,
                "tokens_used": tokens_used,
            }

            # 9. Suggestions — generate and stream immediately (awaited, short timeout inside agent)
            suggestions = await SuggestionAgent.generate(
                user_message=input.user_message,
                assistant_response=full_response,
                mode=input.mode,
                organization_id=input.organization_id,
            )
            if suggestions:
                log.info(
                    "orchestrator.suggestions_generated",
                    trace_id=input.trace_id,
                    conversation_id=input.conversation_id,
                    suggestions_count=len(suggestions),
                )
                yield {"type": "suggestions", "questions": suggestions}

            # 10. Token quota update (fire-and-forget)
            token_task = asyncio.create_task(
                QuotaService.increment_tokens(input.user_id, input.organization_id, tokens_used)
            )
            token_task.add_done_callback(
                lambda t: log.error(
                    "orchestrator.token_quota_update_failed", error=str(t.exception())
                ) if not t.cancelled() and t.exception() else None
            )

            # 12. Analytics (fire-and-forget with error logging)
            if settings.ENABLE_ANALYTICS_AGENT:
                task = asyncio.create_task(
                    self._analytics.run(
                        AnalyticsInput(
                            organization_id=input.organization_id,
                            user_id=input.user_id,
                            trace_id=input.trace_id,
                            conversation_id=input.conversation_id,
                            message_id=assistant_msg_id,
                            query_text=input.user_message,
                            response_time_ms=elapsed_ms,
                            tokens_used=tokens_used,
                            documents_retrieved=len(chunks),
                        )
                    )
                )
                task.add_done_callback(
                    lambda t: log.error(
                        "orchestrator.analytics_failed", error=str(t.exception())
                    ) if not t.cancelled() and t.exception() else None
                )

        except Exception as exc:
            log.error("orchestrator.stream_error", error=str(exc), exc_info=True)
            yield {"type": "error", "error": str(exc)}

    async def _run_guards(self, input: OrchestratorInput) -> None:
        """Apply all request gates before routing/retrieval."""
        log.info(
            "orchestrator.guards_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            user_id=input.user_id,
            organization_id=input.organization_id,
        )
        # 0. Quota check + increment (raises QuotaExceededException if over limit)
        await QuotaService.check_and_increment(input.user_id, input.organization_id)

        # 1. Content policy check (raises on injection/violation)
        await ContentPolicyService.check_user_input(
            text=input.user_message,
            user_id=input.user_id,
            organization_id=input.organization_id,
        )
        log.info(
            "orchestrator.guards_completed",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
        )

    async def _route_and_load_history(self, input: OrchestratorInput) -> tuple[Any, list[dict[str, Any]]]:
        """Run routing and history load concurrently to reduce latency."""
        started_at = time.monotonic()
        log.info(
            "orchestrator.route_and_history_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            context_scope=input.context_scope,
            context_id=input.context_id,
        )
        router_out, history = await asyncio.gather(
            self._router.run(
                RouterInput(
                    organization_id=input.organization_id,
                    user_id=input.user_id,
                    trace_id=input.trace_id,
                    message=input.user_message,
                    context_scope=input.context_scope,
                    context_id=input.context_id,
                    mode=input.mode,
                    toolset=input.toolset,
                )
            ),
            self._persistence.load_history(
                input.conversation_id,
                settings.MAX_HISTORY_MESSAGES,
                log,
            ),
        )
        log.info(
            "orchestrator.route_and_history_completed",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            intent=router_out.intent,
            routed_context_scope=router_out.context_scope,
            routed_context_id=router_out.context_id,
            history_messages=len(history),
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
        )
        return router_out, history

    async def _retrieve_context(
        self,
        input: OrchestratorInput,
        router_out: Any,
    ) -> tuple[list[RetrievedChunk], list[dict], bool]:
        """
        Retrieve internal/web context based on mode and intent.

        Strict routing rules:
        - rag:  Pinecone/OpenSearch only, no web search
        - web:  web search only, no Pinecone
        - auto: hybrid (Pinecone + web), respects chitchat intent
        - general: never reaches this method (short-circuited above)
        """
        chunks: list[RetrievedChunk] = []
        web_sources: list[dict] = []

        # RAG mode always retrieves — never skips to chitchat based on intent.
        # AUTO mode honours CHITCHAT intent to skip unnecessary retrieval.
        is_chitchat = router_out.intent == "CHITCHAT" and input.mode == "auto"

        log.info(
            "orchestrator.retrieve_context_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            mode=input.mode,
            intent=router_out.intent,
            routed_context_scope=router_out.context_scope,
            routed_context_id=router_out.context_id,
            is_chitchat=is_chitchat,
        )

        if is_chitchat:
            log.info(
                "orchestrator.retrieve_context_skipped_for_chitchat",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                mode=input.mode,
            )
            return chunks, web_sources, is_chitchat

        if input.mode == "auto":
            log.info(
                "orchestrator.retrieve_context_parallel",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                mode=input.mode,
            )
            retrieval_out, web_out = await asyncio.gather(
                self._retrieval.run(
                    RetrievalInput(
                        organization_id=input.organization_id,
                        user_id=input.user_id,
                        trace_id=input.trace_id,
                        query=input.user_message,
                        context_scope=router_out.context_scope,
                        context_id=router_out.context_id,
                        context_options=input.context_options,
                        role_ids=input.role_ids,
                        project_ids=input.project_ids,
                        bearer_token=input.bearer_token,
                    )
                ),
                self._web_search.run(
                    WebSearchInput(
                        organization_id=input.organization_id,
                        user_id=input.user_id,
                        trace_id=input.trace_id,
                        query=input.user_message,
                        max_results=5,
                    )
                ),
            )
            chunks = retrieval_out.chunks
            web_sources = web_out.sources
            log.info(
                "orchestrator.retrieve_context_parallel_completed",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                chunks_count=len(chunks),
                web_sources_count=len(web_sources),
            )
            return chunks, web_sources, is_chitchat

        if input.mode == "rag":
            log.info(
                "orchestrator.retrieve_context_rag",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
            )
            retrieval_out = await self._retrieval.run(
                RetrievalInput(
                    organization_id=input.organization_id,
                    user_id=input.user_id,
                    trace_id=input.trace_id,
                    query=input.user_message,
                    context_scope=router_out.context_scope,
                    context_id=router_out.context_id,
                    context_options=input.context_options,
                    role_ids=input.role_ids,
                    project_ids=input.project_ids,
                    bearer_token=input.bearer_token,
                )
            )
            chunks = retrieval_out.chunks
            log.info(
                "orchestrator.retrieve_context_rag_completed",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                chunks_count=len(chunks),
            )
            return chunks, web_sources, is_chitchat

        if input.mode == "web":
            log.info(
                "orchestrator.retrieve_context_web",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
            )
            web_out = await self._web_search.run(
                WebSearchInput(
                    organization_id=input.organization_id,
                    user_id=input.user_id,
                    trace_id=input.trace_id,
                    query=input.user_message,
                    max_results=5,
                )
            )
            web_sources = web_out.sources
            log.info(
                "orchestrator.retrieve_context_web_completed",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                web_sources_count=len(web_sources),
            )

        return chunks, web_sources, is_chitchat

    def _determine_chat_mode(
        self,
        is_chitchat: bool,
        mode: str,
        chunks: list[RetrievedChunk],
        web_sources: list[dict],
    ) -> str:
        """Backward-compatible wrapper around context builder helper."""
        return self._context_builder.determine_chat_mode(is_chitchat, mode, chunks, web_sources)

    def _build_web_context(self, web_sources: list[dict]) -> str:
        """Backward-compatible wrapper around context builder helper."""
        return self._context_builder.build_web_context(web_sources)

    @staticmethod
    def _should_abstain_for_missing_evidence(
        input: OrchestratorInput,
        chunks: list[RetrievedChunk],
        web_sources: list[dict],
        is_chitchat: bool,
    ) -> bool:
        if is_chitchat:
            return False
        if input.mode != "rag":
            return False
        return not chunks and not web_sources

    async def _stream_insufficient_evidence(
        self,
        input: OrchestratorInput,
        router_out: Any,
        start_ms: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        response = (
            "Mình chưa tìm thấy bằng chứng đủ tin cậy trong phạm vi tài liệu đã chọn để trả lời. "
            "Bạn có thể mở rộng phạm vi tìm kiếm hoặc chọn thêm tài liệu liên quan."
        )
        await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="user",
            content=input.user_message,
            log=log,
            mode=input.mode,
            intent=router_out.intent,
            model_config_id=input.model_config_id,
        )
        for token in [response]:
            yield {"type": "token", "content": token}
        elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        tokens_used = self._estimate_tokens(response)
        assistant_msg_id = await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="assistant",
            content=response,
            log=log,
            mode=input.mode,
            intent=router_out.intent,
            tokens_used=tokens_used,
            documents_retrieved=0,
            response_time_ms=elapsed_ms,
            model_config_id=input.model_config_id,
        )
        yield {
            "type": "done",
            "message_id": assistant_msg_id,
            "citations": [],
            "web_sources": [],
            "response_time_ms": elapsed_ms,
            "tokens_used": tokens_used,
            "grounded": False,
            "abstained": True,
            "evidence_status": "insufficient_evidence",
        }

    async def _load_history(self, conversation_id: str) -> list[dict[str, Any]]:
        """Backward-compatible wrapper around persistence helper."""
        return await self._persistence.load_history(conversation_id, settings.MAX_HISTORY_MESSAGES, log)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Rough token estimate: ~4 chars per token (works for EN/VI mixed text)."""
        return max(1, len(text) // 4)

    async def _handle_general_mode(
        self,
        input: OrchestratorInput,
        history: list[dict[str, Any]],
        start_ms: float,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        GENERAL mode fast path: guard → history load → direct LLM.
        No router LLM call, no retrieval, no tool planning.
        This eliminates the ~20-30 second router overhead for plain chat.
        """
        chat_input = ChatInput(
            organization_id=input.organization_id,
            user_id=input.user_id,
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            user_message=input.user_message,
            retrieved_chunks=[],
            history=history,
            mode="direct",
            web_context="",
            model_config_id=input.model_config_id,
        )

        user_msg_id = await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="user",
            content=input.user_message,
            log=log,
            mode=input.mode,
            intent="CHITCHAT",
            model_config_id=input.model_config_id,
        )
        log.info(
            "orchestrator.general_user_message_saved",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            user_message_id=user_msg_id,
        )

        tokens_list: list[str] = []
        stream_tokens_used = 0
        stream_model_used: Optional[str] = None

        def _on_complete(tokens_used: int, model_name: str) -> None:
            nonlocal stream_tokens_used, stream_model_used
            stream_tokens_used = max(stream_tokens_used, int(tokens_used or 0))
            if model_name:
                stream_model_used = model_name

        async for token in self._chat.stream_response(chat_input, on_complete=_on_complete):
            tokens_list.append(token)
            yield {"type": "token", "content": token}

        full_response = "".join(tokens_list)
        elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        tokens_used = stream_tokens_used or self._estimate_tokens(full_response)

        resolved_config = await AIModelConfigService.resolve_model_config(
            purpose=AIModelPurpose.CHAT_COMPLETION,
            selected_model_config_id=input.model_config_id,
            organization_id=input.organization_id,
        )
        model_name = resolved_config.model_display_name or resolved_config.model_name
        model_used = stream_model_used or resolved_config.model_name

        assistant_msg_id = await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="assistant",
            content=full_response,
            log=log,
            mode=input.mode,
            intent="CHITCHAT",
            model_name=model_name,
            model_used=model_used,
            model_config_id=input.model_config_id,
            tokens_used=tokens_used,
            documents_retrieved=0,
            response_time_ms=elapsed_ms,
        )
        log.info(
            "orchestrator.general_assistant_message_saved",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            assistant_message_id=assistant_msg_id,
            elapsed_ms=elapsed_ms,
            tokens_used=tokens_used,
        )

        yield {
            "type": "done",
            "message_id": assistant_msg_id,
            "citations": [],
            "web_sources": [],
            "response_time_ms": elapsed_ms,
            "tokens_used": tokens_used,
        }

        suggestions = await SuggestionAgent.generate(
            user_message=input.user_message,
            assistant_response=full_response,
            mode=input.mode,
            organization_id=input.organization_id,
        )
        if suggestions:
            yield {"type": "suggestions", "questions": suggestions}

        asyncio.create_task(
            QuotaService.increment_tokens(input.user_id, input.organization_id, tokens_used)
        )

    async def _handle_tool_flow(
        self,
        input: OrchestratorInput,
        history: list[dict[str, Any]],
        allowed_tools: list[str],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Handle TOOL_QUERY intent: plan tool → safeguard → execute → chat."""
        start_ms = time.monotonic()

        if not allowed_tools:
            log.warning(
                "orchestrator.tool_flow_disabled",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                toolset=input.toolset,
            )
            yield {
                "type": "error",
                "error": "Phiên chat này đang tắt tool. Hãy đổi toolset nếu bạn muốn dùng task, ticket hoặc project.",
            }
            return

        log.info(
            "orchestrator.tool_flow_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            toolset=input.toolset,
            allowed_tools=allowed_tools,
            history_messages=len(history),
        )
        # 1. Tool Planning
        yield {"type": "tool_call", "content": "Đang phân tích yêu cầu..."}

        planning_out = await self._tool_planning.run(
            ToolPlanningInput(
                organization_id=input.organization_id,
                user_id=input.user_id,
                trace_id=input.trace_id,
                message=input.user_message,
                context_scope=input.context_scope,
                context_id=input.context_id,
                allowed_tools=allowed_tools,
            )
        )

        if not planning_out.success or not planning_out.decision:
            log.warning(
                "orchestrator.tool_planning_failed",
                error=planning_out.error,
                trace_id=input.trace_id,
            )
            planner_error = (planning_out.error or "").lower()
            if "llm tool invocation failed" in planner_error or "tool planning failed" in planner_error:
                friendly_error = (
                    "Mình đang gặp lỗi nội bộ khi phân tích yêu cầu bằng tool. "
                    "Bạn thử lại sau ít phút nhé."
                )
            else:
                friendly_error = (
                    "Mình chưa hiểu rõ yêu cầu của bạn. "
                    "Bạn có thể diễn đạt lại hoặc thử hỏi về task, project, ticket, hoặc daily report nhé."
                )
            yield {
                "type": "error",
                "error": friendly_error,
            }
            return

        decision = planning_out.decision
        log.info(
            "orchestrator.tool_decision",
            tool_name=decision.tool_name,
            trace_id=input.trace_id,
        )

        # 2. Safeguard check
        safeguard = SafeguardGate.check(decision.tool_name, decision.params)
        log.info(
            "orchestrator.safeguard_checked",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            tool_name=decision.tool_name,
            allowed=safeguard.allowed,
            requires_confirmation=safeguard.requires_confirmation,
        )

        if not safeguard.allowed:
            log.warning(
                "orchestrator.safeguard_blocked",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                tool_name=decision.tool_name,
                reason=safeguard.reason,
            )
            yield {"type": "error", "error": safeguard.reason}
            return

        # 3. WRITE → require confirmation
        if safeguard.requires_confirmation:
            db = get_database()
            repo = PendingActionRepository(db)
            pending = PendingActionSchema(
                conversation_id=input.conversation_id,
                user_id=input.user_id,
                organization_id=input.organization_id,
                tool_name=decision.tool_name,
                params=decision.params,
                preview=safeguard.preview,
            )
            await repo.create(pending)
            log.info(
                "orchestrator.confirmation_created",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                action_id=pending.id,
                tool_name=decision.tool_name,
            )

            yield {
                "type": "confirm_required",
                "action_id": pending.id,
                "preview": safeguard.preview,
                "tool_name": decision.tool_name,
            }
            return

        # 4. READ → execute immediately
        yield {"type": "tool_call", "content": f"Đang thực hiện: {safeguard.preview}..."}

        tool_result = await ToolExecutor.execute(
            tool_name=decision.tool_name,
            params=decision.params,
            ctx=ToolContext(
                organization_id=input.organization_id,
                user_id=input.user_id,
                bearer_token=input.bearer_token,
                trace_id=input.trace_id,
            ),
        )
        log.info(
            "orchestrator.tool_executed",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            tool_name=decision.tool_name,
            success=tool_result.success,
            has_error=bool(tool_result.error),
        )

        yield {"type": "tool_result", "data": asdict(tool_result)}

        # 5. ChatAgent generates natural language response from tool result
        tool_context = self._format_tool_result(asdict(tool_result))
        chat_input = ChatInput(
            organization_id=input.organization_id,
            user_id=input.user_id,
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            user_message=input.user_message,
            retrieved_chunks=[],  # No RAG for tool queries
            history=history,
            mode="direct",
            model_config_id=input.model_config_id,
        )
        # Inject tool result into system prompt via extra context
        chat_input.extra["tool_context"] = tool_context

        tokens_list: list[str] = []
        stream_tokens_used = 0
        stream_model_used: Optional[str] = None

        def _on_stream_complete(tokens_used: int, model_name: str) -> None:
            nonlocal stream_tokens_used, stream_model_used
            stream_tokens_used = max(stream_tokens_used, int(tokens_used or 0))
            if model_name:
                stream_model_used = model_name

        async for token in self._chat.stream_response(chat_input, on_complete=_on_stream_complete):
            tokens_list.append(token)
            yield {"type": "token", "content": token}

        full_response = "".join(tokens_list)
        elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        tokens_used = stream_tokens_used or self._estimate_tokens(full_response)

        # Save messages
        user_msg_id = await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="user",
            content=input.user_message,
            log=log,
            mode="tool",
            intent="TOOL_QUERY",
            model_config_id=input.model_config_id,
        )
        log.info(
            "orchestrator.tool_user_message_saved",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            user_message_id=user_msg_id,
        )

        resolved_config = await AIModelConfigService.resolve_model_config(
            purpose=AIModelPurpose.CHAT_COMPLETION,
            selected_model_config_id=input.model_config_id,
            organization_id=input.organization_id,
        )
        model_name = resolved_config.model_display_name or resolved_config.model_name
        model_used = stream_model_used or resolved_config.model_name

        assistant_msg_id = await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="assistant",
            content=full_response,
            log=log,
            mode="tool",
            intent="TOOL_QUERY",
            model_name=model_name,
            model_used=model_used,
            model_config_id=input.model_config_id,
            tokens_used=tokens_used,
            response_time_ms=elapsed_ms,
        )
        log.info(
            "orchestrator.tool_assistant_message_saved",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            assistant_message_id=assistant_msg_id,
            tokens_used=tokens_used,
            response_time_ms=elapsed_ms,
        )

        yield {
            "type": "done",
            "message_id": assistant_msg_id,
            "citations": [],
            "web_sources": [],
            "response_time_ms": elapsed_ms,
            "tokens_used": tokens_used,
        }

    async def _handle_confirmed_action(
        self,
        input: OrchestratorInput,
        pending_action: PendingActionSchema,
        history: list[dict[str, Any]],
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Execute a previously confirmed pending action."""
        start_ms = time.monotonic()
        log.info(
            "orchestrator.confirmed_action_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            action_id=pending_action.id,
            tool_name=pending_action.tool_name,
        )

        # Save user confirmation message
        await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="user",
            content=input.user_message,
            log=log,
            mode="tool",
            intent="TOOL_QUERY_CONFIRMED",
            model_config_id=input.model_config_id,
        )

        yield {"type": "tool_call", "content": f"Đang thực hiện: {pending_action.preview}..."}

        db = get_database()
        repo = PendingActionRepository(db)
        try:
            tool_result = await ToolExecutor.execute(
                tool_name=pending_action.tool_name,
                params=pending_action.params,
                ctx=ToolContext(
                    organization_id=input.organization_id,
                    user_id=input.user_id,
                    bearer_token=input.bearer_token,
                    trace_id=input.trace_id,
                ),
            )
        except Exception as exc:
            await repo.update_status(pending_action.id, "failed", error=str(exc))
            log.error(
                "orchestrator.confirmed_action_tool_failed",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                action_id=pending_action.id,
                tool_name=pending_action.tool_name,
                error=str(exc),
                exc_info=True,
            )
            yield {
                "type": "tool_result",
                "data": {
                    "success": False,
                    "tool": pending_action.tool_name,
                    "error": str(exc),
                },
            }
            yield {"type": "error", "error": str(exc), "code": "tool_execution_failed", "retryable": True}
            return
        log.info(
            "orchestrator.confirmed_action_tool_executed",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            action_id=pending_action.id,
            tool_name=pending_action.tool_name,
            success=tool_result.success,
            has_error=bool(tool_result.error),
        )

        yield {"type": "tool_result", "data": asdict(tool_result)}

        # Update pending action status only after the backend action result is known.
        if tool_result.success:
            await repo.transition_status(
                pending_action.id,
                from_status="confirmed",
                to_status="executed",
            )
        else:
            await repo.update_status(
                pending_action.id,
                "failed",
                error=tool_result.error or "Tool execution failed",
            )
            yield {"type": "error", "error": tool_result.error or "Tool execution failed", "code": "tool_execution_failed", "retryable": True}
            return
        log.info(
            "orchestrator.confirmed_action_executed",
            action_id=pending_action.id,
            tool_name=pending_action.tool_name,
            trace_id=input.trace_id,
        )

        # Generate chat response
        tool_context = self._format_tool_result(asdict(tool_result))
        chat_input = ChatInput(
            organization_id=input.organization_id,
            user_id=input.user_id,
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            user_message=input.user_message,
            retrieved_chunks=[],
            history=history,
            mode="direct",
            model_config_id=input.model_config_id,
        )
        chat_input.extra["tool_context"] = tool_context

        tokens_list: list[str] = []
        stream_tokens_used = 0
        stream_model_used: Optional[str] = None

        def _on_stream_complete(tokens_used: int, model_name: str) -> None:
            nonlocal stream_tokens_used, stream_model_used
            stream_tokens_used = max(stream_tokens_used, int(tokens_used or 0))
            if model_name:
                stream_model_used = model_name

        async for token in self._chat.stream_response(chat_input, on_complete=_on_stream_complete):
            tokens_list.append(token)
            yield {"type": "token", "content": token}

        full_response = "".join(tokens_list)
        elapsed_ms = int((time.monotonic() - start_ms) * 1000)
        tokens_used = stream_tokens_used or self._estimate_tokens(full_response)

        resolved_config = await AIModelConfigService.resolve_model_config(
            purpose=AIModelPurpose.CHAT_COMPLETION,
            selected_model_config_id=input.model_config_id,
            organization_id=input.organization_id,
        )
        model_name = resolved_config.model_display_name or resolved_config.model_name
        model_used = stream_model_used or resolved_config.model_name

        assistant_msg_id = await self._persistence.save_message(
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            user_id=input.user_id,
            role="assistant",
            content=full_response,
            log=log,
            mode="tool",
            intent="TOOL_QUERY_CONFIRMED",
            model_name=model_name,
            model_used=model_used,
            model_config_id=input.model_config_id,
            tokens_used=tokens_used,
            response_time_ms=elapsed_ms,
        )
        log.info(
            "orchestrator.confirmed_action_response_saved",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            action_id=pending_action.id,
            assistant_message_id=assistant_msg_id,
            tokens_used=tokens_used,
            response_time_ms=elapsed_ms,
        )

        yield {
            "type": "done",
            "message_id": assistant_msg_id,
            "citations": [],
            "web_sources": [],
            "response_time_ms": elapsed_ms,
            "tokens_used": tokens_used,
        }

    @staticmethod
    def _format_tool_result(tool_result: dict[str, Any]) -> str:
        """Format tool execution result for LLM context."""
        if not tool_result.get("success"):
            return f"Kết quả thực thi: Thất bại. {tool_result.get('error', 'Unknown error')}"
        data = tool_result.get("data", {})
        return f"Kết quả thực thi (JSON): {__import__('json').dumps(data, ensure_ascii=False, default=str)}"

    async def _save_message(
        self,
        conversation_id: str,
        organization_id: str,
        user_id: str,
        role: str,
        content: str,
        mode: Optional[str] = None,
        intent: Optional[str] = None,
        citations: Optional[list[CitationSchema]] = None,
        web_sources: Optional[list[dict]] = None,
        model_name: Optional[str] = None,
        model_used: Optional[str] = None,
        model_config_id: Optional[str] = None,
        tokens_used: Optional[int] = None,
        documents_retrieved: Optional[int] = None,
        response_time_ms: Optional[int] = None,
    ) -> str:
        """Backward-compatible wrapper around persistence helper."""
        return await self._persistence.save_message(
            conversation_id=conversation_id,
            organization_id=organization_id,
            user_id=user_id,
            role=role,
            content=content,
            log=log,
            mode=mode,
            intent=intent,
            citations=citations,
            web_sources=web_sources,
            model_name=model_name,
            model_used=model_used,
            model_config_id=model_config_id,
            tokens_used=tokens_used,
            documents_retrieved=documents_retrieved,
            response_time_ms=response_time_ms,
        )
