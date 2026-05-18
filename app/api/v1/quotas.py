from __future__ import annotations

from datetime import datetime

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.dependencies import CurrentUser, DBSession, InternalRequest
from app.db.repositories.quota_repo import QuotaRepository
from app.schemas.common import ApiResponse, ErrorResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/quotas", tags=["Quotas"])


class OrgQuotaResponse(BaseModel):
    organization_id: str
    monthly_message_limit: int
    monthly_messages_used: int
    token_limit: int
    tokens_used: int
    tokens_remaining: int
    monthly_ingestion_limit: int
    monthly_ingestions_used: int
    reset_at: datetime


class UserQuotaResponse(BaseModel):
    user_id: str
    organization_id: str
    daily_message_limit: int
    daily_messages_used: int
    daily_token_limit: int
    daily_tokens_used: int
    daily_tokens_remaining: int
    reset_at: datetime


class QuotaMeResponse(BaseModel):
    user: UserQuotaResponse
    organization: OrgQuotaResponse


class UpdateOrgQuotaRequest(BaseModel):
    monthly_message_limit: int | None = None
    monthly_ingestion_limit: int | None = None
    token_limit: int | None = None


def _build_org_quota_response(org_quota) -> OrgQuotaResponse:
    return OrgQuotaResponse(
        organization_id=org_quota.organization_id,
        monthly_message_limit=org_quota.monthly_message_limit,
        monthly_messages_used=org_quota.monthly_messages_used,
        token_limit=org_quota.token_limit,
        tokens_used=org_quota.tokens_used,
        tokens_remaining=max(0, org_quota.token_limit - org_quota.tokens_used),
        monthly_ingestion_limit=org_quota.monthly_ingestion_limit,
        monthly_ingestions_used=org_quota.monthly_ingestions_used,
        reset_at=org_quota.reset_at,
    )


@router.get(
    "/me",
    response_model=ApiResponse[QuotaMeResponse],
    summary="Get current user's quota status",
    responses={401: {"model": ErrorResponse}},
)
async def get_my_quota(
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[QuotaMeResponse]:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    repo = QuotaRepository(db)
    org_quota = await repo.get_or_create_org_quota(user.organization_id)
    user_quota = await repo.get_or_create_user_quota(user.user_id, user.organization_id)

    return ApiResponse(
        statusCode=200,
        message="success",
        data=QuotaMeResponse(
            user=UserQuotaResponse(
                user_id=user_quota.user_id,
                organization_id=user_quota.organization_id,
                daily_message_limit=user_quota.daily_message_limit,
                daily_messages_used=user_quota.daily_messages_used,
                daily_token_limit=user_quota.daily_token_limit,
                daily_tokens_used=user_quota.daily_tokens_used,
                daily_tokens_remaining=max(0, user_quota.daily_token_limit - user_quota.daily_tokens_used),
                reset_at=user_quota.reset_at,
            ),
            organization=_build_org_quota_response(org_quota),
        ),
    )


@router.get(
    "/org",
    response_model=ApiResponse[OrgQuotaResponse],
    summary="Get organization quota",
    responses={401: {"model": ErrorResponse}, 403: {"model": ErrorResponse}},
)
async def get_org_quota(
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[OrgQuotaResponse]:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    repo = QuotaRepository(db)
    org_quota = await repo.get_or_create_org_quota(user.organization_id)

    return ApiResponse(
        statusCode=200,
        message="success",
        data=_build_org_quota_response(org_quota),
    )


@router.get(
    "/org/internal",
    response_model=ApiResponse[OrgQuotaResponse],
    summary="Get organization quota (internal only)",
    responses={403: {"model": ErrorResponse}},
)
async def get_org_quota_internal(
    _: InternalRequest,
    db: DBSession,
    org_id: str | None = None,
) -> ApiResponse[OrgQuotaResponse]:
    if not org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="org_id query param required")

    repo = QuotaRepository(db)
    org_quota = await repo.get_or_create_org_quota(org_id)

    return ApiResponse(
        statusCode=200,
        message="success",
        data=_build_org_quota_response(org_quota),
    )


@router.put(
    "/org",
    response_model=ApiResponse[OrgQuotaResponse],
    summary="Update org quota limits (internal only)",
    responses={403: {"model": ErrorResponse}},
)
async def update_org_quota(
    body: UpdateOrgQuotaRequest,
    _: InternalRequest,
    db: DBSession,
    org_id: str | None = None,
) -> ApiResponse[OrgQuotaResponse]:
    if not org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="org_id query param required")

    repo = QuotaRepository(db)
    org_quota = await repo.get_or_create_org_quota(org_id)

    update_data: dict = {}
    if body.monthly_message_limit is not None:
        update_data["monthly_message_limit"] = body.monthly_message_limit
    if body.monthly_ingestion_limit is not None:
        update_data["monthly_ingestion_limit"] = body.monthly_ingestion_limit
    if body.token_limit is not None:
        update_data["token_limit"] = body.token_limit

    if update_data:
        from datetime import datetime as dt
        update_data["updated_at"] = dt.utcnow()
        await db["org_quotas"].update_one(
            {"organization_id": org_id},
            {"$set": update_data},
        )
        org_quota = await repo.get_or_create_org_quota(org_id)

    return ApiResponse(
        statusCode=200,
        message="success",
        data=_build_org_quota_response(org_quota),
    )
