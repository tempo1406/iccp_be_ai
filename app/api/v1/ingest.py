from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.clients.be_core_client import BeCoreClient
from app.core.dependencies import CurrentUser, DBSession, InternalRequest
from app.db.repositories.ingest_job_repo import IngestJobRepository
from app.schemas.common import ApiResponse, ErrorResponse
from app.schemas.ingest import (
    DeleteDocumentRequest,
    IngestDocumentRequest,
    IngestJobResponse,
    IngestJobStatusResponse,
    TriggerIngestRequest,
)
from app.services.opensearch_service import OpenSearchService
from app.services.pinecone_service import PineconeService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["Ingestion"])

_COMMON_INGEST_RESPONSES: dict = {
    403: {
        "model": ErrorResponse,
        "description": "Invalid or missing `X-Internal-Key` header",
        "content": {
            "application/json": {
                "example": {"error": "forbidden", "message": "Invalid or missing internal API key"}
            }
        },
    },
    422: {
        "model": ErrorResponse,
        "description": "Document content violates content policy",
    },
    503: {
        "model": ErrorResponse,
        "description": "Pinecone vector store unavailable",
    },
}


@router.post(
    "/documents",
    response_model=ApiResponse[IngestJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger document ingestion",
    description=(
        "Nhận yêu cầu ingestion từ **iccp_be_core** sau khi user upload file.\n\n"
        "Tạo một `IngestJob` record và đưa vào **Celery queue** để xử lý bất đồng bộ.\n\n"
        "**Lưu ý:** Trả về `202 Accepted` ngay lập tức. "
        "Dùng `GET /ingest/jobs/{job_id}` để kiểm tra trạng thái."
    ),
    responses={
        202: {"description": "Job đã được tạo và đưa vào queue thành công"},
        **_COMMON_INGEST_RESPONSES,
    },
)
async def ingest_document(
    body: IngestDocumentRequest,
    _: InternalRequest,
    db: DBSession,
) -> ApiResponse[IngestJobResponse]:
    log.info(
        "ingest.requested",
        document_id=body.document_id,
        organization_id=body.organization_id,
        file_name=body.file_name,
        file_type=body.file_type,
        access_scope=body.access_scope,
        folder_id=body.folder_id,
        project_id=body.project_id,
        category_id=body.category_id,
        uploaded_by=body.uploaded_by,
    )
    repo = IngestJobRepository(db)
    job = await repo.create(
        document_id=body.document_id,
        organization_id=body.organization_id,
    )

    from app.workers.tasks.ingest_tasks import ingest_document_task
    async_result = ingest_document_task.apply_async(
        kwargs={
            "job_id": str(job.id),
            "document_id": body.document_id,
            "organization_id": body.organization_id,
            "file_path": body.file_path,
            "file_name": body.file_name,
            "file_type": body.file_type,
            "access_scope": body.access_scope,
            "folder_id": body.folder_id,
            "project_id": body.project_id,
            "category_id": body.category_id,
            "uploaded_by": body.uploaded_by,
            "folder_path_ids": body.folder_path_ids,
            "role_ids": body.role_ids,
            "user_id": body.user_id if hasattr(body, "user_id") else None,
            "bearer_token": body.bearer_token,
        },
        queue="ingest",
    )

    log.info(
        "ingest.job_queued",
        job_id=str(job.id),
        celery_task_id=str(async_result.id),
        document_id=body.document_id,
        organization_id=body.organization_id,
    )

    return ApiResponse(
        statusCode=202,
        message="Ingestion job queued",
        data=IngestJobResponse(
            job_id=str(job.id),
            document_id=body.document_id,
            organization_id=body.organization_id,
            status="pending",
            message="Ingestion job queued",
        ),
    )


@router.post(
    "/documents/{document_id}/retry",
    response_model=ApiResponse[IngestJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="Retry a failed ingestion job",
    responses={
        202: {"description": "Retry job queued"},
        **_COMMON_INGEST_RESPONSES,
    },
)
async def retry_ingest(
    document_id: str,
    body: IngestDocumentRequest,
    _: InternalRequest,
    db: DBSession,
) -> ApiResponse[IngestJobResponse]:
    # Block retry for policy_rejected documents
    repo = IngestJobRepository(db)
    latest = await repo.get_latest_by_document(document_id)
    if latest and latest.status == "policy_rejected":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot retry a policy_rejected document",
        )

    job = await repo.create(
        document_id=document_id,
        organization_id=body.organization_id,
    )

    from app.workers.tasks.ingest_tasks import ingest_document_task
    async_result = ingest_document_task.apply_async(
        kwargs={
            "job_id": str(job.id),
            "document_id": document_id,
            "organization_id": body.organization_id,
            "file_path": body.file_path,
            "file_name": body.file_name,
            "file_type": body.file_type,
            "access_scope": body.access_scope,
            "folder_id": body.folder_id,
            "project_id": body.project_id,
            "category_id": body.category_id,
            "uploaded_by": body.uploaded_by,
            "folder_path_ids": body.folder_path_ids,
            "role_ids": body.role_ids,
            "bearer_token": body.bearer_token,
        },
        queue="ingest",
    )

    log.info(
        "ingest.retry_job_queued",
        job_id=str(job.id),
        celery_task_id=str(async_result.id),
        document_id=document_id,
        organization_id=body.organization_id,
    )

    return ApiResponse(
        statusCode=202,
        message="Retry ingestion job queued",
        data=IngestJobResponse(
            job_id=str(job.id),
            document_id=document_id,
            organization_id=body.organization_id,
            status="pending",
            message="Retry ingestion job queued",
        ),
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_200_OK,
    summary="Remove document vectors from Pinecone + OpenSearch",
    responses={
        200: {"description": "Vectors deleted from both stores"},
        **_COMMON_INGEST_RESPONSES,
    },
)
async def delete_document_vectors(
    document_id: str,
    body: DeleteDocumentRequest,
    _: InternalRequest,
) -> ApiResponse:
    namespace = f"org_{body.organization_id}"
    await asyncio.gather(
        PineconeService.delete_by_filter(
            namespace=namespace,
            filter={"document_id": {"$eq": document_id}},
        ),
        OpenSearchService.delete_by_document_id(
            organization_id=body.organization_id,
            document_id=document_id,
        ),
        return_exceptions=True,
    )
    log.info(
        "ingest.vectors_deleted",
        document_id=document_id,
        organization_id=body.organization_id,
    )
    return ApiResponse(statusCode=200, message="Vectors deleted successfully")


@router.post(
    "/documents/trigger",
    response_model=ApiResponse[IngestJobResponse],
    status_code=status.HTTP_202_ACCEPTED,
    summary="User-triggered document ingestion",
    description=(
        "FE gọi endpoint này khi user bấm nút 'Kích hoạt AI Index'.\n\n"
        "be_ai tự lấy file info từ be_core qua internal API, sau đó queue Celery task.\n\n"
        "Yêu cầu: JWT của user + x-organization-id header."
    ),
)
async def trigger_ingest_by_user(
    body: TriggerIngestRequest,
    request: Request,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[IngestJobResponse]:
    """User-facing trigger — auth via JWT (not internal key)."""
    if not user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="x-organization-id header required",
        )

    organization_id = user.organization_id
    document_id = body.document_id
    log.info(
        "ingest.user_trigger_requested",
        document_id=document_id,
        organization_id=organization_id,
        user_id=user.user_id,
        has_authorization=bool(request.headers.get("authorization")),
    )
    auth_header = request.headers.get("authorization")
    bearer_token = (
        auth_header.removeprefix("Bearer ").strip()
        if auth_header and auth_header.startswith("Bearer ")
        else None
    )

    if not bearer_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )

    # Fail closed before resolving document metadata: same-org is not enough.
    try:
        acl_result = await BeCoreClient.batch_access_check(
            user_id=user.user_id,
            organization_id=organization_id,
            document_ids=[document_id],
            role_ids=user.role_ids,
            project_ids=user.project_ids,
            bearer_token=bearer_token,
        )
    except Exception as exc:
        log.error(
            "ingest.user_trigger_acl_check_failed",
            document_id=document_id,
            organization_id=organization_id,
            user_id=user.user_id,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to verify document access",
        ) from exc

    allowed_ids = set(acl_result.get("allowed", []))
    if document_id not in allowed_ids:
        log.warning(
            "ingest.user_trigger_forbidden",
            document_id=document_id,
            organization_id=organization_id,
            user_id=user.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found or inaccessible",
        )

    # Lấy file info từ be_core
    try:
        doc_info = await BeCoreClient.get_document_info(
            document_id,
            bearer_token=bearer_token,
        )
        log.info(
            "ingest.user_trigger_document_resolved",
            document_id=document_id,
            organization_id=organization_id,
            file_name=doc_info.file_name,
            file_type=doc_info.file_type,
            access_scope=doc_info.access_scope,
            folder_id=doc_info.folder_id,
            project_id=doc_info.project_id,
            category_id=doc_info.category_id,
            uploaded_by=doc_info.uploaded_by,
            folder_path_ids=doc_info.folder_path_ids,
        )
    except Exception as exc:
        log.error("ingest.get_doc_info_failed", document_id=document_id, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found or inaccessible",
        ) from exc

    # Verify org match
    if doc_info.organization_id != organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    repo = IngestJobRepository(db)
    job = await repo.create(
        document_id=document_id,
        organization_id=organization_id,
    )

    from app.workers.tasks.ingest_tasks import ingest_document_task

    async_result = ingest_document_task.apply_async(
        kwargs={
            "job_id": str(job.id),
            "document_id": document_id,
            "organization_id": organization_id,
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
            "user_id": user.user_id,
            "bearer_token": bearer_token,
        },
        queue="ingest",
    )

    log.info(
        "ingest.user_trigger_queued",
        job_id=str(job.id),
        celery_task_id=str(async_result.id),
        document_id=document_id,
        user_id=user.user_id,
    )

    return ApiResponse(
        statusCode=202,
        message="Ingestion job queued",
        data=IngestJobResponse(
            job_id=str(job.id),
            document_id=document_id,
            organization_id=organization_id,
            status="pending",
            message="Ingestion job queued",
        ),
    )


@router.get(
    "/jobs/{job_id}",
    response_model=ApiResponse[IngestJobStatusResponse],
    summary="Get ingestion job status",
    responses={
        200: {"description": "Job status"},
        403: _COMMON_INGEST_RESPONSES[403],
        404: {"model": ErrorResponse, "description": "Job not found"},
    },
)
async def get_job_status(
    job_id: str,
    _: InternalRequest,
    db: DBSession,
) -> ApiResponse[IngestJobStatusResponse]:
    repo = IngestJobRepository(db)
    job = await repo.get_by_id(job_id)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Ingestion job {job_id} not found",
        )

    return ApiResponse(
        statusCode=200,
        message="success",
        data=IngestJobStatusResponse(
            job_id=job.id,
            document_id=job.document_id,
            organization_id=job.organization_id,
            status=job.status,
            chunks_count=job.chunks_count,
            error_message=job.error_message,
            started_at=job.started_at,
            completed_at=job.completed_at,
            created_at=job.created_at,
        ),
    )
