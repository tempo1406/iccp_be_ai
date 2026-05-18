from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.core.dependencies import SystemAdminUser
from app.schemas.ai_model_config import (
    AIModelConfigCreateRequest,
    AIModelConfigListResponse,
    AIModelConfigResponse,
    AIModelConfigUpdateRequest,
    AIModelProvider,
    AIModelPurpose,
)
from app.schemas.common import ApiResponse, ErrorResponse, SuccessResponse
from app.services.ai_model_config_service import AIModelConfigService

router = APIRouter(prefix="/admin/ai-model-configs", tags=["AI Model Configs"])


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ApiResponse[AIModelConfigResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] Create AI model config",
)
async def create_ai_model_config(
    body: AIModelConfigCreateRequest,
    current_user: SystemAdminUser,
) -> ApiResponse[AIModelConfigResponse]:
    data = await AIModelConfigService.create_config(body, current_user)
    return ApiResponse(statusCode=201, message="success", data=data)


@router.get(
    "",
    response_model=ApiResponse[AIModelConfigListResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] List AI model configs",
)
async def list_ai_model_configs(
    _: SystemAdminUser,
    provider: AIModelProvider | None = None,
    purpose: AIModelPurpose | None = None,
    is_enabled: bool | None = None,
    include_deleted: bool = Query(default=False),
) -> ApiResponse[AIModelConfigListResponse]:
    items = await AIModelConfigService.list_configs(
        provider=provider,
        purpose=purpose,
        is_enabled=is_enabled,
        include_deleted=include_deleted,
    )
    return ApiResponse(
        statusCode=200,
        message="success",
        data=AIModelConfigListResponse(items=items, total=len(items)),
    )


@router.get(
    "/{config_id}",
    response_model=ApiResponse[AIModelConfigResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] Get AI model config detail",
)
async def get_ai_model_config(
    config_id: str,
    _: SystemAdminUser,
    include_deleted: bool = Query(default=False),
) -> ApiResponse[AIModelConfigResponse]:
    data = await AIModelConfigService.get_config(config_id, include_deleted=include_deleted)
    return ApiResponse(statusCode=200, message="success", data=data)


@router.patch(
    "/{config_id}",
    response_model=ApiResponse[AIModelConfigResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] Update AI model config",
)
async def update_ai_model_config(
    config_id: str,
    body: AIModelConfigUpdateRequest,
    current_user: SystemAdminUser,
) -> ApiResponse[AIModelConfigResponse]:
    data = await AIModelConfigService.update_config(config_id, body, current_user)
    return ApiResponse(statusCode=200, message="success", data=data)


@router.delete(
    "/{config_id}",
    response_model=ApiResponse[SuccessResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] Soft delete AI model config",
)
async def delete_ai_model_config(
    config_id: str,
    current_user: SystemAdminUser,
) -> ApiResponse[SuccessResponse]:
    await AIModelConfigService.soft_delete_config(config_id, current_user)
    return ApiResponse(statusCode=200, message="success", data=SuccessResponse())


@router.patch(
    "/{config_id}/enable",
    response_model=ApiResponse[AIModelConfigResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] Enable AI model config",
)
async def enable_ai_model_config(
    config_id: str,
    current_user: SystemAdminUser,
) -> ApiResponse[AIModelConfigResponse]:
    data = await AIModelConfigService.set_enabled(config_id, True, current_user)
    return ApiResponse(statusCode=200, message="success", data=data)


@router.patch(
    "/{config_id}/disable",
    response_model=ApiResponse[AIModelConfigResponse],
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    summary="[SYSTEM_ADMIN] Disable AI model config",
)
async def disable_ai_model_config(
    config_id: str,
    current_user: SystemAdminUser,
) -> ApiResponse[AIModelConfigResponse]:
    data = await AIModelConfigService.set_enabled(config_id, False, current_user)
    return ApiResponse(statusCode=200, message="success", data=data)
