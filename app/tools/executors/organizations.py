from __future__ import annotations

from typing import Any

from app.clients.be_core_client import BeCoreClient

from ..base import ToolContext
from ..schemas.organizations import GetOrgProfileInput, ListOrgMembersInput


class OrganizationExecutor:
    @staticmethod
    async def get_org_profile(input: GetOrgProfileInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.get_org_profile(
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def list_org_members(input: ListOrgMembersInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.list_org_members(
            page=input.page,
            limit=input.limit,
            search=input.search,
            bearer_token=ctx.bearer_token,
        )
