from __future__ import annotations

from datetime import datetime
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.schemas.ai_model_config import AIModelConfigSchema

_COLLECTION = "ai_model_configs"


class AIModelConfigRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._col = db[_COLLECTION]

    async def create(self, config: AIModelConfigSchema) -> AIModelConfigSchema:
        doc = config.model_dump()
        doc["_id"] = doc["id"]
        await self._col.insert_one(doc)
        return config

    async def get_by_id(
        self,
        config_id: str,
        *,
        include_deleted: bool = False,
    ) -> Optional[AIModelConfigSchema]:
        query: dict = {"_id": config_id}
        if not include_deleted:
            query["deleted_at"] = None

        doc = await self._col.find_one(query)
        if not doc:
            return None

        return self._to_schema(doc)

    async def list_configs(
        self,
        *,
        provider: Optional[str] = None,
        purpose: Optional[str] = None,
        is_enabled: Optional[bool] = None,
        include_deleted: bool = False,
    ) -> list[AIModelConfigSchema]:
        query: dict = {}

        if not include_deleted:
            query["deleted_at"] = None
        if provider:
            query["provider"] = provider
        if purpose:
            query["purpose_codes"] = purpose
        if is_enabled is not None:
            query["is_enabled"] = is_enabled

        cursor = self._col.find(query).sort(
            [
                ("priority", 1),
                ("updated_at", -1),
                ("created_at", -1),
            ]
        )
        docs = await cursor.to_list(length=None)
        return [self._to_schema(doc) for doc in docs]

    async def update(
        self,
        config_id: str,
        data: dict,
    ) -> Optional[AIModelConfigSchema]:
        data["updated_at"] = datetime.utcnow()
        updated = await self._col.find_one_and_update(
            {"_id": config_id, "deleted_at": None},
            {"$set": data},
            return_document=True,
        )
        if not updated:
            return None

        return self._to_schema(updated)

    async def soft_delete(
        self,
        config_id: str,
        *,
        updated_by: Optional[str] = None,
    ) -> bool:
        now = datetime.utcnow()
        payload: dict = {"deleted_at": now, "updated_at": now, "is_enabled": False}
        if updated_by:
            payload["updated_by"] = updated_by
        result = await self._col.update_one(
            {"_id": config_id, "deleted_at": None},
            {"$set": payload},
        )
        return result.modified_count > 0

    async def set_enabled(
        self,
        config_id: str,
        is_enabled: bool,
        *,
        updated_by: Optional[str] = None,
    ) -> Optional[AIModelConfigSchema]:
        payload: dict = {"is_enabled": is_enabled, "updated_at": datetime.utcnow()}
        if updated_by:
            payload["updated_by"] = updated_by

        updated = await self._col.find_one_and_update(
            {"_id": config_id, "deleted_at": None},
            {"$set": payload},
            return_document=True,
        )
        if not updated:
            return None

        return self._to_schema(updated)

    async def resolve_for_purpose(
        self,
        purpose: str,
        subscription_plan_code: Optional[str] = None,
    ) -> Optional[AIModelConfigSchema]:
        query: dict = {
            "deleted_at": None,
            "is_enabled": True,
            "purpose_codes": purpose,
        }

        if subscription_plan_code:
            query["$or"] = [
                {"applies_to_all_plans": True},
                {"allowed_subscription_plan_codes": subscription_plan_code},
            ]
        else:
            query["applies_to_all_plans"] = True

        doc = await self._col.find_one(
            query,
            sort=[
                ("priority", 1),
                ("updated_at", -1),
                ("created_at", -1),
            ],
        )
        if not doc:
            return None

        return self._to_schema(doc)

    async def list_available_for_subscription(
        self,
        *,
        purpose: Optional[str] = None,
        subscription_plan_code: Optional[str] = None,
    ) -> list[AIModelConfigSchema]:
        query: dict = {
            "deleted_at": None,
            "is_enabled": True,
        }
        if purpose:
            query["purpose_codes"] = purpose

        if subscription_plan_code:
            query["$or"] = [
                {"applies_to_all_plans": True},
                {"allowed_subscription_plan_codes": subscription_plan_code},
            ]
        else:
            query["applies_to_all_plans"] = True

        cursor = self._col.find(query).sort(
            [
                ("priority", 1),
                ("updated_at", -1),
                ("created_at", -1),
            ]
        )
        docs = await cursor.to_list(length=None)
        return [self._to_schema(doc) for doc in docs]

    def _to_schema(self, doc: dict) -> AIModelConfigSchema:
        payload = dict(doc)
        payload.pop("_id", None)
        return AIModelConfigSchema(**payload)
