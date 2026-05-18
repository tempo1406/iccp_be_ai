from __future__ import annotations

from typing import Optional

import structlog
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.core.config import settings

log = structlog.get_logger(__name__)

_client: Optional[AsyncIOMotorClient] = None
_database: Optional[AsyncIOMotorDatabase] = None


def get_database() -> AsyncIOMotorDatabase:
    """Return the current MongoDB database instance."""
    if _database is None:
        raise RuntimeError("MongoDB not initialized. Call init_mongodb() first.")
    return _database


async def init_mongodb() -> None:
    """Initialize Motor async MongoDB client and create indexes. Idempotent."""
    global _client, _database

    if _database is not None:
        return  # Already initialized (e.g. Celery worker called twice)

    _client = AsyncIOMotorClient(settings.MONGODB_URL)
    _database = _client[settings.MONGODB_DATABASE]

    log.info("mongodb.connected", url=settings.MONGODB_URL, database=settings.MONGODB_DATABASE)

    await _create_indexes(_database)
    log.info("mongodb.indexes_created")


async def close_mongodb() -> None:
    """Close MongoDB connection."""
    global _client, _database
    if _client:
        _client.close()
        _client = None
        _database = None
        log.info("mongodb.disconnected")


async def _create_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create all required indexes."""

    # conversations indexes
    await db["conversations"].create_indexes([
        IndexModel([("organization_id", ASCENDING)]),
        IndexModel([("user_id", ASCENDING)]),
        IndexModel([("status", ASCENDING)]),
        IndexModel([("organization_id", ASCENDING), ("user_id", ASCENDING), ("status", ASCENDING)]),
        IndexModel([("created_at", DESCENDING)]),
        # Sparse: only indexes docs where deleted_at exists (soft-delete queries)
        IndexModel([("deleted_at", ASCENDING)], sparse=True),
    ])

    # messages indexes
    await db["messages"].create_indexes([
        IndexModel([("conversation_id", ASCENDING)]),
        IndexModel([("conversation_id", ASCENDING), ("created_at", ASCENDING)]),
        IndexModel([("organization_id", ASCENDING)]),
        IndexModel([("user_id", ASCENDING)]),
    ])

    # ingest_jobs indexes
    await db["ingest_jobs"].create_indexes([
        IndexModel([("document_id", ASCENDING)]),
        IndexModel([("organization_id", ASCENDING)]),
        IndexModel([("document_id", ASCENDING), ("created_at", DESCENDING)]),
    ])

    # org_quotas indexes
    await db["org_quotas"].create_indexes([
        IndexModel([("organization_id", ASCENDING)], unique=True),
    ])

    # user_quotas indexes
    await db["user_quotas"].create_indexes([
        IndexModel([("user_id", ASCENDING), ("organization_id", ASCENDING)], unique=True),
    ])

    # ai_model_configs indexes
    await db["ai_model_configs"].create_indexes([
        IndexModel([("is_enabled", ASCENDING)]),
        IndexModel([("deleted_at", ASCENDING)], sparse=True),
        IndexModel([("provider", ASCENDING)]),
        IndexModel([("purpose_codes", ASCENDING)]),
        IndexModel([("priority", ASCENDING), ("updated_at", DESCENDING)]),
        IndexModel([("allowed_subscription_plan_codes", ASCENDING)]),
    ])
