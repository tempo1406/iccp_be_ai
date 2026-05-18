from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, status

from app.clients.be_core_client import BeCoreClient
from app.core.dependencies import InternalRequest
from app.schemas.common import ApiResponse
from app.schemas.ingest import SyncDocumentMetadataRequest

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/internal/documents", tags=["Internal Documents"])


@router.patch(
    "/{document_id}/metadata",
    response_model=ApiResponse[dict],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resync indexed document metadata",
    description=(
        "Nhận tín hiệu từ be_core khi metadata ACL/scope của document thay đổi. "
        "Hiện tại be_ai xử lý bằng cách queue reindex lại document với metadata mới."
    ),
)
async def sync_document_metadata(
    document_id: str,
    body: SyncDocumentMetadataRequest,
    _: InternalRequest,
) -> ApiResponse[dict]:
    log.info(
        "internal_documents.metadata_sync_requested",
        document_id=document_id,
        access_scope=body.access_scope,
        folder_id=body.folder_id,
    )

    try:
        doc_info = await BeCoreClient.get_document_info(document_id)
    except Exception as exc:
        log.error(
            "internal_documents.metadata_sync_failed_to_resolve",
            document_id=document_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        ) from exc

    if not doc_info.file_path or not doc_info.file_type:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Document metadata incomplete for reindex",
        )

    from app.workers.tasks.ingest_tasks import ingest_document_task

    async_result = ingest_document_task.apply_async(
        kwargs={
            "job_id": None,
            "document_id": document_id,
            "organization_id": doc_info.organization_id,
            "file_path": doc_info.file_path,
            "file_name": doc_info.file_name,
            "file_type": doc_info.file_type,
            "access_scope": doc_info.access_scope,
            "folder_id": doc_info.folder_id,
            "project_id": doc_info.project_id,
            "category_id": doc_info.category_id,
            "uploaded_by": doc_info.uploaded_by,
            "folder_path_ids": doc_info.folder_path_ids,
            "role_ids": [],
            "user_id": None,
            "bearer_token": None,
        },
        queue="ingest",
    )

    log.info(
        "internal_documents.metadata_sync_queued",
        document_id=document_id,
        organization_id=doc_info.organization_id,
        celery_task_id=str(async_result.id),
        access_scope=doc_info.access_scope,
        folder_id=doc_info.folder_id,
        project_id=doc_info.project_id,
    )

    return ApiResponse(
        statusCode=202,
        message="Metadata sync queued",
        data={
            "document_id": document_id,
            "organization_id": doc_info.organization_id,
            "task_id": str(async_result.id),
        },
    )
