from __future__ import annotations

from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.schemas.pending_action import PendingActionSchema


class PendingActionRepository:
    def __init__(self, db: AsyncIOMotorDatabase):
        self._collection = db["pending_actions"]

    async def create(self, action: PendingActionSchema) -> PendingActionSchema:
        doc = action.model_dump()
        doc["_id"] = action.id
        await self._collection.insert_one(doc)
        return action

    async def get_by_id(self, action_id: str) -> Optional[PendingActionSchema]:
        doc = await self._collection.find_one({"_id": action_id})
        if not doc:
            return None
        doc["id"] = doc.pop("_id")
        return PendingActionSchema(**doc)

    async def get_by_conversation(
        self,
        conversation_id: str,
        status: Optional[str] = None,
    ) -> list[PendingActionSchema]:
        query: dict = {"conversation_id": conversation_id}
        if status:
            query["status"] = status
        cursor = self._collection.find(query).sort("created_at", -1)
        results = []
        async for doc in cursor:
            doc["id"] = doc.pop("_id")
            results.append(PendingActionSchema(**doc))
        return results

    async def update_status(
        self,
        action_id: str,
        status: str,
        *,
        error: Optional[str] = None,
    ) -> bool:
        update: dict = {"status": status}
        if status == "confirmed":
            update["confirmed_at"] = datetime.utcnow()
        elif status == "executed":
            update["executed_at"] = datetime.utcnow()
        elif status == "failed":
            update["failed_at"] = datetime.utcnow()
        elif status == "cancelled":
            update["cancelled_at"] = datetime.utcnow()
        if error is not None:
            update["error"] = error
        result = await self._collection.update_one(
            {"_id": action_id},
            {"$set": update},
        )
        return result.modified_count > 0

    async def transition_status(
        self,
        action_id: str,
        *,
        from_status: str,
        to_status: str,
        error: Optional[str] = None,
    ) -> bool:
        update: dict = {"status": to_status}
        now = datetime.utcnow()
        if to_status == "confirmed":
            update["confirmed_at"] = now
        elif to_status == "executed":
            update["executed_at"] = now
        elif to_status == "failed":
            update["failed_at"] = now
        elif to_status == "cancelled":
            update["cancelled_at"] = now
        if error is not None:
            update["error"] = error
        result = await self._collection.update_one(
            {"_id": action_id, "status": from_status},
            {"$set": update},
        )
        return result.modified_count > 0

    async def cancel_pending_by_conversation(self, conversation_id: str) -> int:
        result = await self._collection.update_many(
            {"conversation_id": conversation_id, "status": "pending"},
            {"$set": {"status": "cancelled"}},
        )
        return result.modified_count

    async def delete_expired(self) -> int:
        result = await self._collection.delete_many(
            {"expires_at": {"$lt": datetime.utcnow()}, "status": "pending"}
        )
        return result.deleted_count
