from __future__ import annotations

from typing import Any

from app.clients.be_core_client import BeCoreClient

from ..base import ToolContext
from ..schemas.daily_reports import GetDailyReportInput, SubmitDailyReportInput


class DailyReportExecutor:
    @staticmethod
    async def get_daily_report(input: GetDailyReportInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.get_daily_report(
            project_id=input.project_id,
            date=input.date,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def submit_daily_report(input: SubmitDailyReportInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.submit_daily_report(
            project_id=input.project_id,
            report_id=input.report_id,
            bearer_token=ctx.bearer_token,
        )
