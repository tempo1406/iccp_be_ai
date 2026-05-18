from __future__ import annotations

from typing import Any

from app.clients.be_core_client import BeCoreClient

from ..base import ToolContext
from ..schemas.projects import GetProjectDetailInput, ListProjectsInput


class ProjectExecutor:
    @staticmethod
    async def list_projects(input: ListProjectsInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.list_projects(
            page=input.page,
            limit=input.limit,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def get_project_detail(input: GetProjectDetailInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.get_project_detail(
            project_id=input.project_id,
            bearer_token=ctx.bearer_token,
        )
