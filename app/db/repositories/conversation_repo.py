from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.schemas.conversation import ConversationSchema

log = structlog.get_logger(__name__)

_COLLECTION = "conversations"


class ConversationRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._col = db[_COLLECTION]

    async def create(
        self,
        org_id: str,
        user_id: str,
        title: Optional[str] = None,
        mode: str = "general",
        toolset: str = "none",
    ) -> ConversationSchema:
        """Create and persist a new conversation."""
        conversation = ConversationSchema(
            organization_id=org_id,
            user_id=user_id,
            title=title,
            mode=mode,
            toolset=toolset,
        )
        doc = conversation.model_dump(mode="json")
        doc["_id"] = doc["id"]
        await self._col.insert_one(doc)
        return conversation

    async def get_by_id(self, conversation_id: str) -> Optional[ConversationSchema]:
        """Fetch conversation by id. Returns None if not found."""
        doc = await self._col.find_one({"_id": conversation_id, "deleted_at": None})
        if not doc:
            return None
        return self._to_schema(doc)

    async def get_by_user(
        self,
        user_id: str,
        org_id: str,
        status: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[ConversationSchema]:
        """List conversations for a user within an org, sorted newest first."""
        query: dict = {
            "user_id": user_id,
            "organization_id": org_id,
            "deleted_at": None,
        }
        if status:
            query["status"] = status

        cursor = self._col.find(query).sort("created_at", -1).skip(offset).limit(limit)
        docs = await cursor.to_list(length=limit)
        return [self._to_schema(d) for d in docs]

    async def update(self, conversation_id: str, data: dict) -> Optional[ConversationSchema]:
        """Update conversation fields. Returns updated schema or None."""
        data["updated_at"] = datetime.utcnow()
        result = await self._col.find_one_and_update(
            {"_id": conversation_id, "deleted_at": None},
            {"$set": data},
            return_document=True,
        )
        if not result:
            return None
        return self._to_schema(result)

    async def soft_delete(self, conversation_id: str) -> bool:
        """Mark conversation as deleted."""
        result = await self._col.update_one(
            {"_id": conversation_id, "deleted_at": None},
            {"$set": {"deleted_at": datetime.utcnow(), "updated_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    async def archive(self, conversation_id: str) -> bool:
        """Set conversation status to archived."""
        result = await self._col.update_one(
            {"_id": conversation_id, "deleted_at": None},
            {"$set": {"status": "archived", "updated_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    async def restore(self, conversation_id: str) -> bool:
        """Restore archived conversation to active."""
        result = await self._col.update_one(
            {"_id": conversation_id, "deleted_at": None, "status": "archived"},
            {"$set": {"status": "active", "updated_at": datetime.utcnow()}},
        )
        return result.modified_count > 0

    def _to_schema(self, doc: dict) -> ConversationSchema:
        doc = dict(doc)
        doc.pop("_id", None)
        return ConversationSchema(**doc)
