from __future__ import annotations

import asyncio
import logging
from typing import Optional

from celery import Task

from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


def _get_or_create_event_loop():
    """Get existing event loop or create a new one."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
        return loop
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


@celery_app.task(
    bind=True,
    name="app.workers.tasks.ingest_tasks.ingest_document_task",
    max_retries=3,
    default_retry_delay=60,
    queue="ingest",
)
def ingest_document_task(
    self: Task,
    job_id: Optional[str],
    document_id: str,
    organization_id: str,
    file_path: str,
    file_name: str,
    file_type: str,
    access_scope: str = "organization",
    folder_id: Optional[str] = None,
    project_id: Optional[str] = None,
    category_id: Optional[str] = None,
    uploaded_by: Optional[str] = None,
    folder_path_ids: Optional[list[str]] = None,
    role_ids: Optional[list[str]] = None,
    user_id: Optional[str] = None,
    bearer_token: Optional[str] = None,
) -> dict:
    """
    Celery task: run the full ingestion pipeline for a document.
    Idempotent — safe to retry.
    """
    role_ids = role_ids or []
    folder_path_ids = folder_path_ids or []

    async def _run():
        from app.agents.ingestion_agent import IngestionAgent, IngestionInput
        from app.clients.be_core_client import BeCoreClient
        from app.db.mongodb import init_mongodb, get_database
        from app.db.repositories.ingest_job_repo import IngestJobRepository
        from app.services.pinecone_service import PineconeService

        # Services are initialized at worker startup (celery_app.py @worker_init).
        # These calls are idempotent — no-ops if already initialized.
        await PineconeService.initialize()
        await BeCoreClient.initialize()
        await init_mongodb()

        db = get_database()
        repo = IngestJobRepository(db)
        trace_id = job_id or f"metadata-sync:{document_id}"

        # Mark job as started
        if job_id:
            await repo.mark_started(job_id)
            log.info(
                "[ingest_task] marked_started"
                f" celery_task_id={self.request.id} job={job_id} doc={document_id}"
            )
            log.info(f"[ingest_task] started job={job_id} doc={document_id}")
        else:
            log.info(
                "[ingest_task] metadata_sync_started"
                f" celery_task_id={self.request.id} doc={document_id}"
            )

        try:
            agent = IngestionAgent()
            result = await agent.run(
                IngestionInput(
                    organization_id=organization_id,
                    user_id="system",
                    trace_id=trace_id,
                    document_id=document_id,
                    file_path=file_path,
                    file_name=file_name,
                    file_type=file_type,
                    access_scope=access_scope,
                    uploaded_by=uploaded_by,
                    project_id=project_id,
                    folder_id=folder_id,
                    folder_path_ids=folder_path_ids,
                    category_id=category_id,
                    role_ids=role_ids,
                )
            )

            # Update document status in be_core → indexed
            await BeCoreClient.update_document_status(
                document_id,
                "indexed",
                indexed_chunks=result.chunks_count,
                bearer_token=bearer_token,
            )

            # Mark job success in MongoDB
            if job_id:
                await repo.mark_success(job_id, result.chunks_count)
                log.info(
                    f"[ingest_task] success job={job_id} doc={document_id} "
                    f"chunks={result.chunks_count}"
                )
                log.info(
                    "[ingest_task] completed"
                    f" celery_task_id={self.request.id} job={job_id} status=success"
                )
            else:
                log.info(
                    "[ingest_task] metadata_sync_completed"
                    f" celery_task_id={self.request.id} doc={document_id} "
                    f"chunks={result.chunks_count}"
                )

            # Send success notification if user_id is known
            if user_id:
                await BeCoreClient.send_notification(
                    user_id=user_id,
                    title="Tài liệu đã được xử lý",
                    message=f"Tài liệu '{file_name}' đã được index thành công ({result.chunks_count} chunks).",
                    notification_type="success",
                        data={
                            "document_id": document_id,
                            "job_id": job_id,
                            "chunks_count": result.chunks_count,
                        },
                        bearer_token=bearer_token,
                    )

            return {"status": "success", "chunks_count": result.chunks_count}

        except Exception as exc:
            from app.core.exceptions import ContentPolicyViolationException, PromptInjectionException

            error_msg = str(exc)
            log.error(f"[ingest_task] failed job={job_id} error={error_msg}")
            log.error(
                "[ingest_task] completed"
                f" celery_task_id={self.request.id} job={job_id} status=failed"
            )

            is_policy_violation = isinstance(exc, (ContentPolicyViolationException, PromptInjectionException))

            if job_id:
                if is_policy_violation:
                    await repo.mark_policy_rejected(job_id, error_msg)
                else:
                    await repo.mark_failed(job_id, error_msg)

            doc_status = "policy_rejected" if is_policy_violation else "failed"

            try:
                await BeCoreClient.update_document_status(
                    document_id,
                    doc_status,
                    error_msg=error_msg,
                    bearer_token=bearer_token,
                )
            except Exception:
                pass

            # Send failure notification if user_id is known
            if user_id:
                notification_title = (
                    "Tài liệu bị từ chối" if is_policy_violation
                    else "Xử lý tài liệu thất bại"
                )
                notification_msg = (
                    f"Tài liệu '{file_name}' vi phạm chính sách nội dung."
                    if is_policy_violation
                    else f"Xử lý tài liệu '{file_name}' thất bại. Vui lòng thử lại."
                )
                try:
                    await BeCoreClient.send_notification(
                        user_id=user_id,
                        title=notification_title,
                        message=notification_msg,
                        notification_type="error",
                        data={"document_id": document_id, "job_id": job_id, "error": error_msg},
                        bearer_token=bearer_token,
                    )
                except Exception:
                    pass

            if is_policy_violation:
                # Do not retry policy violations
                return {"status": "policy_rejected", "error": error_msg}

            raise

    try:
        loop = _get_or_create_event_loop()
        return loop.run_until_complete(_run())

    except Exception as exc:
        log.warning(
            f"[ingest_task] retrying job={job_id or 'metadata-sync'} "
            f"attempt={self.request.retries} error={exc}"
        )
        raise self.retry(exc=exc)
