from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ListTasksInput(BaseModel):
    """List tasks in a project."""

    project_id: Optional[str] = Field(
        None,
        description="Project ID to filter tasks. If omitted, searches across user's projects.",
    )
    assignee_id: Optional[str] = Field(
        None,
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


class CreateTaskInput(BaseModel):
    """Create a new task in a project."""

    project_id: str = Field(..., description="Project ID to create task in")
    title: str = Field(..., description="Task title")
    description: Optional[str] = Field(None, description="Task description")
    status_id: Optional[str] = Field(None, description="Initial status ID")
    assigned_to: Optional[str] = Field(None, description="User ID to assign task to")
    due_date: Optional[str] = Field(None, description="Due date in ISO format")
    priority: Optional[str] = Field(None, description="LOW | MEDIUM | HIGH | URGENT")


class UpdateTaskStatusInput(BaseModel):
    """Update the status of a task."""

    project_id: str = Field(..., description="Project ID containing the task")
    task_id: str = Field(..., description="Task ID to update")
    status_id: str = Field(..., description="New status ID")


class AddTaskCommentInput(BaseModel):
    """Add a comment to a task."""

    project_id: str = Field(..., description="Project ID")
    task_id: str = Field(..., description="Task ID")
    content: str = Field(..., description="Comment content")
