import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.core.config import settings
from app.schemas.common import TokenPayload


@pytest.mark.asyncio
async def test_ingest_document_returns_202(test_client):
    mock_job = MagicMock()
    mock_job.id = "test-job-id"

    with (
        patch("app.api.v1.ingest.IngestJobRepository") as mock_repo_cls,
        patch("app.workers.tasks.ingest_tasks.ingest_document_task") as mock_task,
    ):
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=mock_job)
        mock_repo_cls.return_value = mock_repo
        mock_task.apply_async = MagicMock()

        response = await test_client.post(
            "/api/v1/ingest/documents",
            json={
                "document_id": "doc-uuid-1",
                "organization_id": "org-uuid-1",
                "file_path": "/app/files/policy.pdf",
                "file_name": "policy.pdf",
                "file_type": "pdf",
                "access_scope": "organization",
            },
            headers={"X-Internal-Key": settings.INTERNAL_API_KEY},
        )

    assert response.status_code == 202
    data = response.json()
    assert data["statusCode"] == 202
    assert data["data"]["status"] == "pending"
    assert data["data"]["document_id"] == "doc-uuid-1"


@pytest.mark.asyncio
async def test_ingest_document_requires_internal_key(test_client):
    response = await test_client.post(
        "/api/v1/ingest/documents",
        json={
            "document_id": "doc-uuid-1",
            "organization_id": "org-uuid-1",
            "file_path": "/app/files/policy.pdf",
            "file_name": "policy.pdf",
            "file_type": "pdf",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_document_vectors_returns_204(test_client):
    with patch("app.api.v1.ingest.PineconeService.delete_by_filter", AsyncMock()):
        response = await test_client.request(
            "DELETE",
            "/api/v1/ingest/documents/doc-uuid-1",
            json={"organization_id": "org-uuid-1"},
            headers={"X-Internal-Key": settings.INTERNAL_API_KEY},
        )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_trigger_ingest_by_user_denies_inaccessible_document(
    test_client,
    valid_jwt_token,
):
    document_id = "d290f1ee-6c54-4b01-90e6-d701748f0851"
    org_id = "a3bb189e-8bf9-3888-9912-ace4e6543002"
    user_id = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"

    with (
        patch(
            "app.core.dependencies.introspect_token",
            AsyncMock(
                return_value=TokenPayload(
                    user_id=user_id,
                    organization_id=org_id,
                    role_ids=["11111111-1111-1111-1111-111111111111"],
                    project_ids=["22222222-2222-2222-2222-222222222222"],
                )
            ),
        ),
        patch(
            "app.api.v1.ingest.BeCoreClient.batch_access_check",
            AsyncMock(return_value={"allowed": [], "denied": [document_id]}),
        ),
        patch("app.api.v1.ingest.BeCoreClient.get_document_info", AsyncMock()) as mock_get_doc,
    ):
        response = await test_client.post(
            "/api/v1/ingest/documents/trigger",
            json={"document_id": document_id},
            headers={"Authorization": f"Bearer {valid_jwt_token}"},
        )

    assert response.status_code == 404
    assert response.json()["detail"] == f"Document {document_id} not found or inaccessible"
    mock_get_doc.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_ingest_by_user_queues_job_after_acl_check(
    test_client,
    valid_jwt_token,
):
    document_id = "d290f1ee-6c54-4b01-90e6-d701748f0851"
    org_id = "a3bb189e-8bf9-3888-9912-ace4e6543002"
    user_id = "f81d4fae-7dec-11d0-a765-00a0c91e6bf6"
    mock_job = MagicMock()
    mock_job.id = "test-job-id"
    doc_info = MagicMock()
    doc_info.organization_id = org_id
    doc_info.file_path = "/app/files/policy.pdf"
    doc_info.file_name = "policy.pdf"
    doc_info.file_type = "pdf"
    doc_info.access_scope = "private"
    doc_info.folder_id = None
    doc_info.project_id = None
    doc_info.category_id = None
    doc_info.uploaded_by = user_id
    doc_info.folder_path_ids = []

    with (
        patch(
            "app.core.dependencies.introspect_token",
            AsyncMock(
                return_value=TokenPayload(
                    user_id=user_id,
                    organization_id=org_id,
                    role_ids=["11111111-1111-1111-1111-111111111111"],
                    project_ids=["22222222-2222-2222-2222-222222222222"],
                )
            ),
        ),
        patch(
            "app.api.v1.ingest.BeCoreClient.batch_access_check",
            AsyncMock(return_value={"allowed": [document_id], "denied": []}),
        ) as mock_acl,
        patch(
            "app.api.v1.ingest.BeCoreClient.get_document_info",
            AsyncMock(return_value=doc_info),
        ),
        patch("app.api.v1.ingest.IngestJobRepository") as mock_repo_cls,
        patch("app.workers.tasks.ingest_tasks.ingest_document_task") as mock_task,
    ):
        mock_repo = AsyncMock()
        mock_repo.create = AsyncMock(return_value=mock_job)
        mock_repo_cls.return_value = mock_repo
        mock_task.apply_async = MagicMock(return_value=MagicMock(id="celery-task-id"))

        response = await test_client.post(
            "/api/v1/ingest/documents/trigger",
            json={"document_id": document_id},
            headers={"Authorization": f"Bearer {valid_jwt_token}"},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload["data"]["document_id"] == document_id
    mock_acl.assert_awaited_once()
    mock_task.apply_async.assert_called_once()
