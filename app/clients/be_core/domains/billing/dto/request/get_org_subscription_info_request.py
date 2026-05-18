from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GetOrgSubscriptionInfoRequest:
    organization_id: str
