from __future__ import annotations

from typing import Any

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.schemas.message import MessageSchema

log = structlog.get_logger(__name__)

_COLLECTION = "messages"


class MessageRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._col = db[_COLLECTION]

    async def create(self, message: MessageSchema) -> MessageSchema:
        """Persist a new message."""
        doc = message.model_dump()
        doc["_id"] = doc["id"]
        await self._col.insert_one(doc)
        return message

    async def get_by_conversation(
        self,
        conversation_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> list[MessageSchema]:
        """Get messages for a conversation, sorted oldest first."""
        cursor = (
            self._col.find({"conversation_id": conversation_id})
            .sort("created_at", 1)
            .skip(offset)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        return [self._to_schema(d) for d in docs]

    async def get_history_for_llm(
        self,
        conversation_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Return simplified message history for LLM context.
        Returns list of {"role": ..., "content": ...} dicts, newest last.
        """
        # Fetch the last `limit` messages sorted oldest→newest
        cursor = (
            self._col.find(
                {"conversation_id": conversation_id},
                {"role": 1, "content": 1, "_id": 0},
            )
            .sort("created_at", -1)
            .limit(limit)
        )
        docs = await cursor.to_list(length=limit)
        # Reverse so oldest messages come first (proper chat order for LLM)
        docs.reverse()
        return [{"role": d["role"], "content": d["content"]} for d in docs]

    async def count_by_conversation(self, conversation_id: str) -> int:
        """Count total messages in a conversation."""
        return await self._col.count_documents({"conversation_id": conversation_id})

    def _to_schema(self, doc: dict) -> MessageSchema:
        doc = dict(doc)
        doc.pop("_id", None)
        return MessageSchema(**doc)
