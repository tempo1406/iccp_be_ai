from __future__ import annotations

from datetime import datetime
from typing import Optional

import structlog
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.db.schemas.ingest_job import IngestJobSchema

log = structlog.get_logger(__name__)

_COLLECTION = "ingest_jobs"


class IngestJobRepository:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self._col = db[_COLLECTION]

    async def create(self, document_id: str, organization_id: str) -> IngestJobSchema:
        """Create and persist a new ingest job with status=pending."""
        job = IngestJobSchema(
            document_id=document_id,
            organization_id=organization_id,
        )
        doc = job.model_dump()
        doc["_id"] = doc["id"]
        await self._col.insert_one(doc)
        return job

    async def get_by_id(self, job_id: str) -> Optional[IngestJobSchema]:
        """Fetch job by id."""
        doc = await self._col.find_one({"_id": job_id})
        if not doc:
            return None
        return self._to_schema(doc)

    async def get_latest_by_document(self, document_id: str) -> Optional[IngestJobSchema]:
        """Get the most recent ingest job for a document."""
        doc = await self._col.find_one(
            {"document_id": document_id},
            sort=[("created_at", -1)],
        )
        if not doc:
            return None
        return self._to_schema(doc)

    async def mark_started(self, job_id: str) -> None:
        """Update job status to started."""
        await self._col.update_one(
            {"_id": job_id},
            {"$set": {
                "status": "started",
                "started_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }},
        )

    async def mark_success(self, job_id: str, chunks_count: int) -> None:
        """Update job status to success with chunk count."""
        await self._col.update_one(
            {"_id": job_id},
            {"$set": {
                "status": "success",
                "chunks_count": chunks_count,
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }},
        )

    async def mark_failed(self, job_id: str, error_message: str) -> None:
        """Update job status to failed with error message."""
        await self._col.update_one(
            {"_id": job_id},
            {"$set": {
                "status": "failed",
                "error_message": error_message,
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }},
        )

    async def mark_policy_rejected(self, job_id: str, error_message: str) -> None:
        """Update job status to policy_rejected."""
        await self._col.update_one(
            {"_id": job_id},
            {"$set": {
                "status": "policy_rejected",
                "error_message": error_message,
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
            }},
        )

    def _to_schema(self, doc: dict) -> IngestJobSchema:
        doc = dict(doc)
        doc.pop("_id", None)
        return IngestJobSchema(**doc)
