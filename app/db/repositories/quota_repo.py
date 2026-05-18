from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import settings
from app.db.schemas.quota import OrgQuotaSchema, UserQuotaSchema

log = structlog.get_logger(__name__)

_ORG_COLLECTION = "org_quotas"
_USER_COLLECTION = "user_quotas"
_VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _next_month_reset() -> datetime:
    """Return the first day of next month at midnight Asia/Ho_Chi_Minh, stored as UTC."""
    now_local = datetime.now(_VIETNAM_TZ)
    if now_local.month == 12:
        next_local = datetime(now_local.year + 1, 1, 1, tzinfo=_VIETNAM_TZ)
    else:
        next_local = datetime(now_local.year, now_local.month + 1, 1, tzinfo=_VIETNAM_TZ)
    return next_local.astimezone(timezone.utc).replace(tzinfo=None)


def _next_day_reset() -> datetime:
    """Return the next local midnight in Asia/Ho_Chi_Minh, stored as UTC."""
    now_local = datetime.now(_VIETNAM_TZ)
    tomorrow = now_local + timedelta(days=1)
    next_local = datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=_VIETNAM_TZ)
    return next_local.astimezone(timezone.utc).replace(tzinfo=None)


class QuotaRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._org_col = db[_ORG_COLLECTION]
        self._user_col = db[_USER_COLLECTION]

    async def get_or_create_org_quota(self, org_id: str) -> OrgQuotaSchema:
        """Get or create org quota. Auto-resets monthly counters if past reset_at."""
        doc = await self._org_col.find_one({"organization_id": org_id})

        if doc:
            update_data: dict[str, Any] = {}
            reset_at = doc.get("reset_at")
            if reset_at and datetime.utcnow() >= reset_at:
                update_data["monthly_messages_used"] = 0
                update_data["monthly_ingestions_used"] = 0
                update_data["reset_at"] = _next_month_reset()

            if doc.get("token_limit") in (None, 100000):
                update_data["token_limit"] = settings.DEFAULT_ORG_TOKEN_LIMIT

            if update_data:
                update_data["updated_at"] = datetime.utcnow()
                await self._org_col.update_one(
                    {"organization_id": org_id},
                    {"$set": update_data},
                )
                doc = await self._org_col.find_one({"organization_id": org_id})

            return self._to_org_schema(doc)

        quota = OrgQuotaSchema(
            organization_id=org_id,
            monthly_message_limit=settings.DEFAULT_MONTHLY_MESSAGE_LIMIT,
            token_limit=settings.DEFAULT_ORG_TOKEN_LIMIT,
            monthly_ingestion_limit=settings.DEFAULT_MONTHLY_INGESTION_LIMIT,
            reset_at=_next_month_reset(),
        )
        doc_to_insert = quota.model_dump()
        doc_to_insert["_id"] = doc_to_insert["id"]
        try:
            await self._org_col.insert_one(doc_to_insert)
        except Exception:
            doc = await self._org_col.find_one({"organization_id": org_id})
            if doc:
                return self._to_org_schema(doc)
            raise
        return quota

    async def increment_org_messages(self, org_id: str) -> None:
        await self._org_col.update_one(
            {"organization_id": org_id},
            {
                "$inc": {"monthly_messages_used": 1},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

    async def increment_org_tokens(self, org_id: str, tokens: int) -> None:
        if tokens <= 0:
            return
        await self._org_col.update_one(
            {"organization_id": org_id},
            {
                "$inc": {"tokens_used": tokens},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

    async def increment_org_ingestions(self, org_id: str) -> None:
        await self._org_col.update_one(
            {"organization_id": org_id},
            {
                "$inc": {"monthly_ingestions_used": 1},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

    async def get_or_create_user_quota(self, user_id: str, org_id: str) -> UserQuotaSchema:
        """Get or create user daily quota. Auto-resets counters if past reset_at."""
        doc = await self._user_col.find_one({"user_id": user_id, "organization_id": org_id})

        if doc:
            update_data: dict[str, Any] = {}
            reset_at = doc.get("reset_at")
            if reset_at and datetime.utcnow() >= reset_at:
                update_data["daily_messages_used"] = 0
                update_data["daily_tokens_used"] = 0
                update_data["reset_at"] = _next_day_reset()

            if doc.get("daily_token_limit") is None:
                update_data["daily_token_limit"] = settings.DEFAULT_DAILY_USER_TOKEN_LIMIT

            if update_data:
                update_data["updated_at"] = datetime.utcnow()
                await self._user_col.update_one(
                    {"user_id": user_id, "organization_id": org_id},
                    {"$set": update_data},
                )
                doc = await self._user_col.find_one({"user_id": user_id, "organization_id": org_id})

            return self._to_user_schema(doc)

        quota = UserQuotaSchema(
            user_id=user_id,
            organization_id=org_id,
            daily_message_limit=settings.DEFAULT_DAILY_USER_MESSAGE_LIMIT,
            daily_token_limit=settings.DEFAULT_DAILY_USER_TOKEN_LIMIT,
            reset_at=_next_day_reset(),
        )
        doc_to_insert = quota.model_dump()
        doc_to_insert["_id"] = doc_to_insert["id"]
        try:
            await self._user_col.insert_one(doc_to_insert)
        except Exception:
            doc = await self._user_col.find_one({"user_id": user_id, "organization_id": org_id})
            if doc:
                return self._to_user_schema(doc)
            raise
        return quota

    async def increment_user_messages(self, user_id: str, org_id: str) -> None:
        await self._user_col.update_one(
            {"user_id": user_id, "organization_id": org_id},
            {
                "$inc": {"daily_messages_used": 1},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

    async def increment_user_tokens(self, user_id: str, org_id: str, tokens: int) -> None:
        if tokens <= 0:
            return
        await self._user_col.update_one(
            {"user_id": user_id, "organization_id": org_id},
            {
                "$inc": {"daily_tokens_used": tokens},
                "$set": {"updated_at": datetime.utcnow()},
            },
        )

    async def check_quota(self, user_id: str, org_id: str) -> dict[str, Any]:
        """Check org and user quotas before a request starts."""
        org_quota = await self.get_or_create_org_quota(org_id)
        user_quota = await self.get_or_create_user_quota(user_id, org_id)

        org_ok = org_quota.monthly_messages_used < org_quota.monthly_message_limit
        user_ok = user_quota.daily_messages_used < user_quota.daily_message_limit
        token_ok = org_quota.tokens_used < org_quota.token_limit
        user_token_ok = user_quota.daily_tokens_used < user_quota.daily_token_limit

        return {
            "org_ok": org_ok,
            "user_ok": user_ok,
            "token_ok": token_ok,
            "user_token_ok": user_token_ok,
            "org_quota": org_quota,
            "user_quota": user_quota,
        }

    def _to_org_schema(self, doc: dict) -> OrgQuotaSchema:
        doc = dict(doc)
        doc.pop("_id", None)
        return OrgQuotaSchema(**doc)

    def _to_user_schema(self, doc: dict) -> UserQuotaSchema:
        doc = dict(doc)
        doc.pop("_id", None)
        return UserQuotaSchema(**doc)