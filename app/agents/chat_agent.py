from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.agents.retrieval_agent import RetrievedChunk
from app.prompts.rag_chat import (
    RAG_SYSTEM_PROMPT,
    RAG_SYSTEM_PROMPT_NO_CONTEXT,
    USER_MESSAGE_WRAPPER,
)
from app.prompts.chitchat import CHITCHAT_SYSTEM_PROMPT
from app.prompts.web_search_chat import (
    WEB_SEARCH_SYSTEM_PROMPT,
    WEB_SEARCH_SYSTEM_PROMPT_NO_RESULTS,
    WEB_USER_MESSAGE_WRAPPER,
)
from app.prompts.hybrid_chat import (
    HYBRID_SYSTEM_PROMPT,
    HYBRID_SYSTEM_PROMPT_DOC_ONLY,
    HYBRID_SYSTEM_PROMPT_WEB_ONLY,
    HYBRID_USER_MESSAGE_WRAPPER,
)
from app.schemas.ai_model_config import AIModelPurpose
from app.services.content_policy_service import ContentPolicyService
from app.services.language_service import LanguageService
from app.services.llm_service import ChatMessage, LLMService

log = structlog.get_logger(__name__)

_MAX_CONTEXT_CHARS = 8000
# Max user message length to pass to LLM (truncate if longer to prevent token abuse)
_MAX_USER_MSG_CHARS = 2000
# Max chars per chunk snippet in context (truncate individually before combining)
_MAX_CHUNK_CHARS = 1500


@dataclass
class ChatInput(AgentInput):
    conversation_id: str = ""
    user_message: str = ""
    retrieved_chunks: list[RetrievedChunk] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    mode: str = "rag"  # rag | direct (chitchat) | web | hybrid
    web_context: str = ""  # pre-formatted web search context
    model_config_id: Optional[str] = None


@dataclass
class CitationInfo:
    document_id: str
    chunk_index: int
    file_name: str
    relevance_score: float
    cited_content: str


@dataclass
class ChatOutput(AgentOutput):
    response_text: str = ""
    citations: list[CitationInfo] = field(default_factory=list)
    tokens_used: int = 0
    model_name: str = ""


class ChatAgent(BaseAgent):
    """
    Generates a chat response using retrieved context (RAG), web search, hybrid, or direct LLM.
    Applies two-layer defense against prompt injection:
      1. User message sanitized + wrapped in <user_question> XML tag
      2. Document chunks sanitized via ContentPolicyService.sanitize_for_context()
         before embedding into the system prompt
    """

    async def run(self, input: AgentInput) -> ChatOutput:
        assert isinstance(input, ChatInput), "Expected ChatInput"

        log.info(
            "chat_agent.run_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            mode=input.mode,
            history_messages=len(input.history),
            retrieved_chunks=len(input.retrieved_chunks),
            has_web_context=bool(input.web_context),
            has_tool_context=bool(input.extra.get("tool_context")),
            model_config_id=input.model_config_id,
        )
        messages = self._build_messages(input)
        response = await LLMService.ainvoke(
            messages,
            organization_id=input.organization_id,
            purpose=AIModelPurpose.CHAT_COMPLETION,
            selected_model_config_id=input.model_config_id,
        )
        citations = self._extract_citations(input.retrieved_chunks)

        log.info(
            "chat_agent.complete",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            tokens=response.tokens_used,
            citations=len(citations),
            mode=input.mode,
        )

        return ChatOutput(
            success=True,
            response_text=response.content,
            citations=citations,
            tokens_used=response.tokens_used,
            model_name=response.model,
        )

    async def stream_response(
        self,
        input: ChatInput,
        *,
        on_complete: Optional[Callable[[int, str], None]] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream LLM response token by token."""
        log.info(
            "chat_agent.stream_started",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            organization_id=input.organization_id,
            mode=input.mode,
            history_messages=len(input.history),
            retrieved_chunks=len(input.retrieved_chunks),
            has_web_context=bool(input.web_context),
            has_tool_context=bool(input.extra.get("tool_context")),
            model_config_id=input.model_config_id,
        )
        messages = self._build_messages(input)

        def _on_llm_complete(result) -> None:
            log.info(
                "chat_agent.stream_completed",
                trace_id=input.trace_id,
                conversation_id=input.conversation_id,
                mode=input.mode,
                tokens_used=result.tokens_used,
                model_name=result.model,
            )
            if on_complete:
                on_complete(result.tokens_used, result.model)

        async for token in LLMService.astream(
            messages,
            organization_id=input.organization_id,
            purpose=AIModelPurpose.CHAT_COMPLETION,
            selected_model_config_id=input.model_config_id,
            on_complete=_on_llm_complete,
        ):
            yield token

    def _build_messages(self, input: ChatInput) -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        response_language_instruction = LanguageService.response_language_instruction_from_text(
            input.user_message
        )

        if input.mode == "direct":
            tool_context = input.extra.get("tool_context", "")
            if tool_context:
                system_content = (
                    "Bạn là trợ lý thông minh. Dưới đây là kết quả từ thao tác người dùng yêu cầu. "
                    "Hãy tóm tắt kết quả một cách tự nhiên, rõ ràng. "
                    "Nếu thao tác thất bại, hãy giải thích lỗi một cách lịch sự.\n\n"
                    f"{tool_context}"
                )
            else:
                system_content = CHITCHAT_SYSTEM_PROMPT
            messages.append(
                ChatMessage(
                    role="system",
                    content=self._append_language_instruction(
                        system_content,
                        response_language_instruction,
                    ),
                )
            )
            wrapper = USER_MESSAGE_WRAPPER

        elif input.mode == "web":
            if input.web_context:
                system_content = WEB_SEARCH_SYSTEM_PROMPT.format(context=input.web_context)
            else:
                system_content = WEB_SEARCH_SYSTEM_PROMPT_NO_RESULTS
            messages.append(
                ChatMessage(
                    role="system",
                    content=self._append_language_instruction(
                        system_content,
                        response_language_instruction,
                    ),
                )
            )
            wrapper = WEB_USER_MESSAGE_WRAPPER

        elif input.mode == "hybrid":
            doc_context = self._build_sanitized_context(input.retrieved_chunks)
            web_context = input.web_context

            if doc_context and web_context:
                system_content = HYBRID_SYSTEM_PROMPT.format(
                    doc_context=doc_context,
                    web_context=web_context,
                )
            elif doc_context:
                system_content = HYBRID_SYSTEM_PROMPT_DOC_ONLY.format(doc_context=doc_context)
            elif web_context:
                system_content = HYBRID_SYSTEM_PROMPT_WEB_ONLY.format(web_context=web_context)
            else:
                system_content = RAG_SYSTEM_PROMPT_NO_CONTEXT
            messages.append(
                ChatMessage(
                    role="system",
                    content=self._append_language_instruction(
                        system_content,
                        response_language_instruction,
                    ),
                )
            )
            wrapper = HYBRID_USER_MESSAGE_WRAPPER

        else:
            # Default: rag
            context = self._build_sanitized_context(input.retrieved_chunks)
            if context:
                system_content = RAG_SYSTEM_PROMPT.format(context=context)
            else:
                system_content = RAG_SYSTEM_PROMPT_NO_CONTEXT
            messages.append(
                ChatMessage(
                    role="system",
                    content=self._append_language_instruction(
                        system_content,
                        response_language_instruction,
                    ),
                )
            )
            wrapper = USER_MESSAGE_WRAPPER

        # Inject conversation history (sanitized)
        for msg in input.history[-10:]:
            role = "human" if msg.get("role") == "user" else "ai"
            raw_content = msg.get("content", "")
            safe_content = ContentPolicyService.sanitize_user_message(raw_content)
            messages.append(ChatMessage(role=role, content=safe_content))

        # Current user message: sanitize + truncate + wrap
        safe_message = ContentPolicyService.sanitize_user_message(input.user_message)
        safe_message = safe_message[:_MAX_USER_MSG_CHARS]
        wrapped_message = wrapper.format(message=safe_message)

        messages.append(ChatMessage(role="human", content=wrapped_message))
        log.debug(
            "chat_agent.messages_built",
            trace_id=input.trace_id,
            conversation_id=input.conversation_id,
            mode=input.mode,
            history_messages=len(input.history[-10:]),
            message_count=len(messages),
            retrieved_chunks=len(input.retrieved_chunks),
            web_context_chars=len(input.web_context),
            user_message_chars=len(safe_message),
        )
        return messages

    def _build_sanitized_context(self, chunks: list[RetrievedChunk]) -> str:
        """
        Build context string from retrieved chunks.
        Each chunk content is:
        - Sanitized to remove injection patterns
        - Truncated per chunk
        - Total context capped at _MAX_CONTEXT_CHARS
        """
        if not chunks:
            return ""

        parts: list[str] = []
        total_chars = 0

        for i, chunk in enumerate(chunks, start=1):
            safe_content = ContentPolicyService.sanitize_for_context(chunk.content)
            safe_content = safe_content[:_MAX_CHUNK_CHARS]

            safe_filename = ContentPolicyService.sanitize_user_message(chunk.file_name)

            snippet = f"[{i}] Nguồn: {safe_filename}\n{safe_content}"

            if total_chars + len(snippet) > _MAX_CONTEXT_CHARS:
                log.debug(
                    "chat_agent.context_truncated",
                    chunks_total=len(chunks),
                    chunks_used=i - 1,
                )
                break
            parts.append(snippet)
            total_chars += len(snippet)

        return "\n\n---\n\n".join(parts)

    def _extract_citations(self, chunks: list[RetrievedChunk]) -> list[CitationInfo]:
        return [
            CitationInfo(
                document_id=c.document_id,
                chunk_index=c.chunk_index,
                file_name=c.file_name,
                relevance_score=round(c.score, 4),
                cited_content=c.content[:300],
            )
            for c in chunks
        ]

    def _append_language_instruction(self, system_content: str, instruction: str) -> str:
        return f"{system_content.rstrip()}\n\n## Ngôn ngữ phản hồi\n- {instruction}"
