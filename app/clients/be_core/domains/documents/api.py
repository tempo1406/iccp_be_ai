from __future__ import annotations

from typing import Any, Optional

from ...http_core import BeCoreHttpCore
from .dto.request.batch_access_check_request import BatchAccessCheckRequest
from .dto.request.update_document_status_request import UpdateDocumentStatusRequest
from .dto.response.batch_access_check_response import BatchAccessCheckResponse
from .dto.response.document_info_response import DocumentInfoResponse


class DocumentsApi(BeCoreHttpCore):
    @staticmethod
    def _auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def list_documents(
        cls, folder_id: Optional[str] = None, category_id: Optional[str] = None,
        project_id: Optional[str] = None, page: Optional[int] = None,
        limit: Optional[int] = None, bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if folder_id:
            params["folderId"] = folder_id
        if category_id:
            params["categoryId"] = category_id
        if project_id:
            params["projectId"] = project_id
        if page:
            params["page"] = page
        if limit:
            params["limit"] = limit
        body = await cls._request("GET", "/api/v1/documents", params=params, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def get_document_tree(
        cls, include_deleted: bool = False, bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if include_deleted:
            params["includeDeleted"] = "true"
        body = await cls._request("GET", "/api/v1/documents/tree", params=params, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def get_document_info(
        cls,
        document_id: str,
        bearer_token: str | None = None,
    ) -> DocumentInfoResponse:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request(
            "GET",
            f"/api/v1/internal/documents/{document_id}",
            headers=headers,
        )
        data = cls._unwrap_api_response(body)
        return DocumentInfoResponse(
            document_id=data["id"],
            organization_id=data.get("organizationId") or "",
            file_path=data["filePath"],
            status=data.get("status", ""),
            access_scope=data.get("accessScope", "organization"),
            file_name=data.get("fileName"),
            file_type=data.get("fileType"),
            folder_id=data.get("folderId"),
            folder_path_ids=list(data.get("folderPathIds") or []),
            project_id=data.get("projectId"),
            category_id=data.get("categoryId"),
            uploaded_by=data.get("uploadedBy"),
            mime_type=data.get("mimeType"),
            title=data.get("title"),
            is_active=bool(data.get("isActive", True)),
            version=int(data.get("version", 1) or 1),
        )

    @classmethod
    async def update_document_status(
        cls,
        document_id: str,
        status: str,
        error_msg: str | None = None,
        indexed_chunks: int | None = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        request_dto = UpdateDocumentStatusRequest(
            document_id=document_id,
            status=status,
            error_msg=error_msg,
        )
        payload: dict[str, Any] = {"status": request_dto.status}
        if request_dto.error_msg:
            payload["errorMessage"] = request_dto.error_msg
        if indexed_chunks is not None:
            payload["indexedChunks"] = indexed_chunks
        body = await cls._request(
            "PATCH",
            f"/api/v1/internal/documents/{document_id}/status",
            json=payload,
            headers=headers,
        )
        return cls._unwrap_api_response(body)

    @classmethod
    async def batch_access_check(
        cls,
        user_id: str,
        organization_id: str,
        document_ids: list[str],
        role_ids: list[str] | None = None,
        project_ids: list[str] | None = None,
        bearer_token: str | None = None,
    ) -> dict[str, list[str]]:
        headers = cls._auth_headers(bearer_token)
        request_dto = BatchAccessCheckRequest(
            user_id=user_id,
            organization_id=organization_id,
            document_ids=document_ids,
            role_ids=role_ids or [],
            project_ids=project_ids or [],
        )
        payload = {
            "userId": request_dto.user_id,
            "organizationId": request_dto.organization_id,
            "documentIds": request_dto.document_ids,
            "roleIds": request_dto.role_ids,
            "projectIds": request_dto.project_ids,
        }
        body = await cls._request(
            "POST",
            "/api/v1/internal/documents/batch-access-check",
            json=payload,
            headers=headers,
        )
        data = cls._unwrap_api_response(body)
        response_dto = BatchAccessCheckResponse(
            allowed=list(data.get("allowed") or []),
            denied=list(data.get("denied") or []),
        )
        return {
            "allowed": response_dto.allowed,
            "denied": response_dto.denied,
        }
