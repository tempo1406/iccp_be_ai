from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict

# Các loại file được phép ingest — phải khớp với be_core ALLOWED_MIME_TYPES và FE accept
AllowedFileType = Literal["pdf", "docx", "xlsx", "xls", "txt", "md", "markdown"]


class IngestDocumentRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                "organization_id": "a3bb189e-8bf9-3888-9912-ace4e6543002",
                "file_path": "/app/uploads/hr_policy_2024.pdf",
                "file_name": "hr_policy_2024.pdf",
                "file_type": "pdf",
                "access_scope": "organization",
                "folder_id": None,
                "project_id": None,
                "category_id": None,
                "uploaded_by": None,
                "folder_path_ids": [],
                "role_ids": [],
                "user_id": None,
            }
        }
    )

    document_id: str = Field(..., description="UUID của document")
    organization_id: str = Field(..., description="UUID của tổ chức (tenant)")
    file_path: str = Field(..., description="Đường dẫn đến file")
    file_name: str = Field(..., description="Tên file gốc")
    file_type: AllowedFileType = Field(
        ...,
        description="Phần mở rộng file: pdf | docx | xlsx | xls | txt | md | markdown",
    )
    access_scope: str = Field(
        default="organization",
        description="Phạm vi truy cập: `private` | `organization` | `project` | `role` | `user`",
    )
    folder_id: Optional[str] = Field(None, description="UUID của folder")
    project_id: Optional[str] = Field(None, description="UUID của project")
    category_id: Optional[str] = Field(None, description="UUID của category")
    uploaded_by: Optional[str] = Field(None, description="UUID của uploader/owner document")
    folder_path_ids: list[str] = Field(
        default_factory=list,
        description="Danh sách folder ancestor IDs từ root -> folder hiện tại",
    )
    role_ids: list[str] = Field(default_factory=list, description="Danh sách UUID role")
    user_id: Optional[str] = Field(None, description="UUID của user để gửi notification khi xong")
    bearer_token: Optional[str] = Field(
        None,
        description="JWT token để be_ai gọi be_core internal endpoints cần Authorization",
    )


class IngestJobResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
                "document_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                "organization_id": "a3bb189e-8bf9-3888-9912-ace4e6543002",
                "status": "pending",
                "message": "Ingestion job queued",
            }
        }
    )

    job_id: str
    document_id: str
    organization_id: str
    status: str = Field(..., description="`pending` | `started` | `success` | `failed` | `policy_rejected`")
    message: str = "Ingestion job queued"


class IngestJobStatusResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "f81d4fae-7dec-11d0-a765-00a0c91e6bf6",
                "document_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
                "organization_id": "a3bb189e-8bf9-3888-9912-ace4e6543002",
                "status": "success",
                "chunks_count": 42,
                "error_message": None,
                "started_at": "2024-01-15T08:30:01Z",
                "completed_at": "2024-01-15T08:30:18Z",
                "created_at": "2024-01-15T08:30:00Z",
            }
        }
    )

    job_id: str
    document_id: str
    organization_id: str
    status: str
    chunks_count: Optional[int] = None
    error_message: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime


class DeleteDocumentRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "organization_id": "a3bb189e-8bf9-3888-9912-ace4e6543002",
            }
        }
    )

    organization_id: str = Field(..., description="UUID của tổ chức sở hữu document")


class TriggerIngestRequest(BaseModel):
    """Request body cho user-facing trigger endpoint (FE gọi với JWT)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "document_id": "d290f1ee-6c54-4b01-90e6-d701748f0851",
            }
        }
    )

    document_id: str = Field(..., description="UUID của document cần index")


class SyncDocumentMetadataRequest(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "accessScope": "project",
                "folderId": "d290f1ee-6c54-4b01-90e6-d701748f0851",
            }
        },
    )

    access_scope: Optional[str] = Field(
        None,
        alias="accessScope",
        description="Access scope mới từ be_core",
    )
    folder_id: Optional[str] = Field(
        None,
        alias="folderId",
        description="Folder ID mới từ be_core",
    )
