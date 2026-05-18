from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    statusCode: int = 200
    message: str = "success"
    data: Optional[T] = None


class TokenPayload(BaseModel):
    user_id: str
    email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    avatar_url: Optional[str] = None
    organization_id: Optional[str] = None
    roles: list[str] = Field(default_factory=list)
    role_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    is_active: bool = True
    is_verified: bool = True


class ErrorResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "error": "unauthorized",
                "message": "Invalid token: signature verification failed",
                "detail": None,
            }
        }
    )

    error: str = Field(..., description="Mã lỗi (snake_case)")
    message: str = Field(..., description="Mô tả lỗi")
    detail: Optional[Any] = Field(None, description="Chi tiết bổ sung (tùy chọn)")


class SuccessResponse(BaseModel):
    message: str = "success"


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    limit: int
    has_next: bool
