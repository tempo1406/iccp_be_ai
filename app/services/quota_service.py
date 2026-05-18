from __future__ import annotations

import structlog

from app.core.exceptions import QuotaExceededException
from app.db.mongodb import get_database
from app.db.repositories.quota_repo import QuotaRepository

log = structlog.get_logger(__name__)


class QuotaService:
    """Manages message and token quota checks and increments."""

    @staticmethod
    async def check_and_increment(user_id: str, org_id: str) -> None:
        """
        Check org and user quotas before a request starts.
        Raises QuotaExceededException if any quota is exceeded.
        Increments org/user message counters on success.
        """
        db = get_database()
        repo = QuotaRepository(db)

        result = await repo.check_quota(user_id, org_id)

        if not result["org_ok"]:
            org_quota = result["org_quota"]
            raise QuotaExceededException(
                message=(
                    f"Organization monthly message quota exceeded "
                    f"({org_quota.monthly_messages_used}/{org_quota.monthly_message_limit}). "
                    "Please contact your administrator."
                ),
                quota_type="org_monthly",
            )

        if not result["token_ok"]:
            raise QuotaExceededException(
                message=(
                    "AI generation is temporarily unavailable. "
                    "Please contact your administrator."
                ),
                quota_type="org_tokens",
            )

        if not result["user_ok"]:
            user_quota = result["user_quota"]
            raise QuotaExceededException(
                message=(
                    f"Daily message limit exceeded "
                    f"({user_quota.daily_messages_used}/{user_quota.daily_message_limit}). "
                    "Please try again tomorrow."
                ),
                quota_type="user_daily",
            )

        if not result["user_token_ok"]:
            user_quota = result["user_quota"]
            raise QuotaExceededException(
                message=(
                    f"Daily token limit exceeded "
                    f"({user_quota.daily_tokens_used}/{user_quota.daily_token_limit}). "
                    "Your token limit resets at 00:00 Asia/Ho_Chi_Minh."
                ),
                quota_type="user_daily_tokens",
            )

        await repo.increment_org_messages(org_id)
        await repo.increment_user_messages(user_id, org_id)

        log.debug("quota_service.incremented", user_id=user_id, org_id=org_id)

    @staticmethod
    async def increment_tokens(user_id: str, org_id: str, tokens: int) -> None:
        """
        Add token usage to both org and user counters.
        Called after streaming completes with the actual estimated token count.
        """
        db = get_database()
        repo = QuotaRepository(db)
        await repo.increment_org_tokens(org_id, tokens)
        await repo.increment_user_tokens(user_id, org_id, tokens)
        log.debug("quota_service.tokens_incremented", user_id=user_id, org_id=org_id, tokens=tokens)

    @staticmethod
    async def check_ingestion_quota(org_id: str) -> None:
        """
        Check org monthly ingestion quota.
        Raises QuotaExceededException if exceeded.
        Increments ingestion counter on success.
        """
        db = get_database()
        repo = QuotaRepository(db)

        org_quota = await repo.get_or_create_org_quota(org_id)

        if org_quota.monthly_ingestions_used >= org_quota.monthly_ingestion_limit:
            raise QuotaExceededException(
                message=(
                    f"Organization monthly ingestion quota exceeded "
                    f"({org_quota.monthly_ingestions_used}/{org_quota.monthly_ingestion_limit}). "
                    "Please contact your administrator."
                ),
                quota_type="ingestion",
            )

        await repo.increment_org_ingestions(org_id)
        log.debug("quota_service.ingestion_incremented", org_id=org_id)