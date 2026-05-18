from __future__ import annotations

from .dto.request.get_org_subscription_info_request import (
    GetOrgSubscriptionInfoRequest,
)
from .dto.response.organization_subscription_info_response import (
    OrganizationSubscriptionInfoResponse,
)
from ...http_core import BeCoreHttpCore


class BeCoreBillingApi(BeCoreHttpCore):
    @classmethod
    async def get_org_subscription_info(
        cls,
        organization_id: str,
    ) -> OrganizationSubscriptionInfoResponse | None:
        request_dto = GetOrgSubscriptionInfoRequest(organization_id=organization_id)
        body = await cls._request(
            "GET",
            "/api/v1/billing/internal/subscription",
            params={"orgId": request_dto.organization_id},
        )
        data = body.get("data") if isinstance(body, dict) else body
        if not data:
            return None

        plan = data.get("plan") or {}
        return OrganizationSubscriptionInfoResponse(
            organization_id=data.get("organizationId") or request_dto.organization_id,
            subscription_id=data.get("id"),
            plan_id=data.get("planId") or plan.get("id"),
            plan_code=plan.get("code"),
            plan_name=plan.get("name"),
            status=data.get("status"),
        )
