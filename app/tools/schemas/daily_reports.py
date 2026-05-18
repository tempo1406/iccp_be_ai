from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class GetDailyReportInput(BaseModel):
    """Get the user's daily report for a project on a specific date."""

    project_id: str = Field(..., description="Project ID")
    date: Optional[str] = Field(
        None,
        description="Date in YYYY-MM-DD format. Defaults to today if not provided.",
    )


class SubmitDailyReportInput(BaseModel):
    """Submit a draft daily report."""

    project_id: str = Field(..., description="Project ID")
    report_id: str = Field(..., description="Daily report ID to submit")
