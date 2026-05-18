from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "service": "iccp_be_ai",
                "version": "1.0.0",
            }
        }
    )

    status: str
    service: str
    version: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
    description=(
        "Kiểm tra service đang hoạt động.\n\n"
        "Dùng cho Docker healthcheck và load balancer probe.\n\n"
        "Không yêu cầu authentication."
    ),
    responses={
        200: {"description": "Service đang hoạt động bình thường"},
    },
)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="iccp_be_ai",
        version="1.0.0",
    )
