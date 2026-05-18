from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrganizationSubscriptionInfoResponse:
    organization_id: str
    subscription_id: str | None = None
    plan_id: str | None = None
    plan_code: str | None = None
    plan_name: str | None = None
    status: str | None = None
