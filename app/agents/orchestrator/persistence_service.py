from __future__ import annotations

from typing import Any, Optional

from app.agents.chat_agent import CitationInfo
from app.agents.citation_utils import CitationPreview
from app.db.schemas.message import CitationSchema, MessageSchema


class OrchestratorPersistenceService:
    """Persistence helpers for orchestrator message/history operations."""

    @staticmethod
    async def load_history(conversation_id: str, max_history_messages: int, log) -> list[dict[str, Any]]:
        """Load recent conversation messages from MongoDB."""
        try:
            from app.db.mongodb import get_database
            from app.db.repositories.message_repo import MessageRepository

            db = get_database()
            repo = MessageRepository(db)
            return await repo.get_history_for_llm(conversation_id, limit=max_history_messages)
        except Exception as exc:
            log.warning("orchestrator.history_load_failed", error=str(exc))
            return []

    @staticmethod
    async def save_message(
        *,
        conversation_id: str,
        organization_id: str,
        user_id: str,
        role: str,
        content: str,
        log,
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
        """Persist one message to MongoDB. Returns the message id."""
        try:
            from app.db.mongodb import get_database
            from app.db.repositories.message_repo import MessageRepository

            db = get_database()
            repo = MessageRepository(db)
            msg = MessageSchema(
                conversation_id=conversation_id,
                organization_id=organization_id,
                user_id=user_id,
                role=role,  # type: ignore[arg-type]
                content=content,
                mode=mode,
                intent=intent,
                citations=citations or [],
                web_sources=web_sources or [],
                model_name=model_name,
                model_used=model_used,
                model_config_id=model_config_id,
                tokens_used=tokens_used,
                documents_retrieved=documents_retrieved,
                response_time_ms=response_time_ms,
            )
            saved = await repo.create(msg)
            return saved.id
        except Exception as exc:
            log.error("orchestrator.save_message_failed", role=role, error=str(exc))
            return ""

    @staticmethod
    def to_citation_schemas(citations: list[CitationInfo | CitationPreview]) -> list[CitationSchema]:
        """Map citation info to Mongo citation schema."""
        return [
            CitationSchema(
                document_id=c.document_id,
                document_name=c.file_name,
                chunk_text=c.cited_content,
                score=c.relevance_score,
                chunk_index=c.chunk_index,
                chunk_count=max(1, int(getattr(c, "chunk_count", 1) or 1)),
            )
            for c in citations
        ]
