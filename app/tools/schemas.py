from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ListTasksInput(BaseModel):
    """List tasks in a project, optionally filtered by assignee and due state."""

    project_id: Optional[str] = Field(
        None,
        description="Project ID to filter tasks. If omitted, searches across user's projects.",
    )
    assignee_id: str = Field(
        ...,
        description="User ID to filter tasks assigned to.",
    )
    due_state: Optional[str] = Field(
        None,
        description="Filter by due state: overdue | due_soon | on_track",
    )


class GetTaskDetailInput(BaseModel):
    """Get detailed information about a specific task."""

    project_id: str = Field(..., description="Project ID containing the task")
    task_id: str = Field(..., description="Task ID to retrieve")


class UpdateTaskStatusInput(BaseModel):
    """Update the status of a task (e.g., mark as Done)."""

    project_id: str = Field(..., description="Project ID containing the task")
    task_id: str = Field(..., description="Task ID to update")
    status_id: str = Field(
        ...,
        description="Status ID to set. Must be a valid status in the project's workflow.",
    )


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
