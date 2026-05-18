from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.core.dependencies import CurrentUser
from app.schemas.ai_model_config import (
    AIModelOptionListResponse,
    AIModelPurpose,
)
from app.schemas.common import ApiResponse, ErrorResponse
from app.services.ai_model_config_service import AIModelConfigService

router = APIRouter(prefix="/ai-models", tags=["AI Models"])


@router.get(
    "/options",
    response_model=ApiResponse[AIModelOptionListResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="[USER] List available AI model options",
)
async def list_available_ai_model_options(
    user: CurrentUser,
    purpose: AIModelPurpose | None = None,
) -> ApiResponse[AIModelOptionListResponse]:
    if not user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization_id required",
        )

    items = await AIModelConfigService.list_available_options_for_user(
        organization_id=user.organization_id,
        purpose=purpose,
    )
    return ApiResponse(
        statusCode=200,
        message="success",
        data=AIModelOptionListResponse(items=items, total=len(items)),
    )
