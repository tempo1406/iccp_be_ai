from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, get_args

from pydantic import BaseModel

from .executors import (
    DailyReportExecutor,
    DocumentExecutor,
    OrganizationExecutor,
    ProjectExecutor,
    TaskExecutor,
    TicketExecutor,
)
from .schemas import (
    AddTaskCommentInput,
    ApproveTicketInput,
    CreateTaskInput,
    CreateTicketInput,
    GetDailyReportInput,
    GetDocumentTreeInput,
    GetOrgProfileInput,
    GetProjectDetailInput,
    GetTaskDetailInput,
    GetTicketDetailInput,
    ListDocumentsInput,
    ListMyTicketsInput,
    ListOrgMembersInput,
    ListProjectsInput,
    ListTasksInput,
    SubmitDailyReportInput,
    UpdateTaskStatusInput,
)

ActionType = Literal["read", "write"]


@dataclass(frozen=True)
class ToolMeta:
    """Metadata for a registered tool."""

    name: str
    toolset: str
    description: str
    schema: type[BaseModel]
    action_type: ActionType
    executor_class: type
    executor_method: str
    requires_project: bool = False


_TOOL_DEFINITIONS: list[ToolMeta] = [
    # ── Tasks ────────────────────────────────────────────────────────────────
    ToolMeta(
        name="list_tasks",
        toolset="tasks",
        description="List tasks in a project filtered by assignee and due state.",
        schema=ListTasksInput,
        action_type="read",
        executor_class=TaskExecutor,
        executor_method="list_tasks",
        requires_project=True,
    ),
    ToolMeta(
        name="get_task_detail",
        toolset="tasks",
        description="Get detailed information about a specific task.",
        schema=GetTaskDetailInput,
        action_type="read",
        executor_class=TaskExecutor,
        executor_method="get_task_detail",
        requires_project=True,
    ),
    ToolMeta(
        name="create_task",
        toolset="tasks",
        description="Create a new task in a project with title, description, assignee, due date, and priority.",
        schema=CreateTaskInput,
        action_type="write",
        executor_class=TaskExecutor,
        executor_method="create_task",
        requires_project=True,
    ),
    ToolMeta(
        name="update_task_status",
        toolset="tasks",
        description="Update the status of a task (e.g., mark as Done, In Progress).",
        schema=UpdateTaskStatusInput,
        action_type="write",
        executor_class=TaskExecutor,
        executor_method="update_task_status",
        requires_project=True,
    ),
    ToolMeta(
        name="add_task_comment",
        toolset="tasks",
        description="Add a comment to a task.",
        schema=AddTaskCommentInput,
        action_type="write",
        executor_class=TaskExecutor,
        executor_method="add_task_comment",
        requires_project=True,
    ),
    # ── Projects ─────────────────────────────────────────────────────────────
    ToolMeta(
        name="list_projects",
        toolset="projects",
        description="List projects the user has access to.",
        schema=ListProjectsInput,
        action_type="read",
        executor_class=ProjectExecutor,
        executor_method="list_projects",
    ),
    ToolMeta(
        name="get_project_detail",
        toolset="projects",
        description="Get project details by ID or slug.",
        schema=GetProjectDetailInput,
        action_type="read",
        executor_class=ProjectExecutor,
        executor_method="get_project_detail",
    ),
    # ── Daily Reports ────────────────────────────────────────────────────────
    ToolMeta(
        name="get_daily_report",
        toolset="daily_reports",
        description="Get the user's daily report for a project on a specific date.",
        schema=GetDailyReportInput,
        action_type="read",
        executor_class=DailyReportExecutor,
        executor_method="get_daily_report",
        requires_project=True,
    ),
    ToolMeta(
        name="submit_daily_report",
        toolset="daily_reports",
        description="Submit a draft daily report.",
        schema=SubmitDailyReportInput,
        action_type="write",
        executor_class=DailyReportExecutor,
        executor_method="submit_daily_report",
        requires_project=True,
    ),
    # ── Tickets ──────────────────────────────────────────────────────────────
    ToolMeta(
        name="list_my_tickets",
        toolset="tickets",
        description="List ticket requests created by or assigned to the user.",
        schema=ListMyTicketsInput,
        action_type="read",
        executor_class=TicketExecutor,
        executor_method="list_my_tickets",
    ),
    ToolMeta(
        name="get_ticket_detail",
        toolset="tickets",
        description="Get ticket request details.",
        schema=GetTicketDetailInput,
        action_type="read",
        executor_class=TicketExecutor,
        executor_method="get_ticket_detail",
    ),
    ToolMeta(
        name="create_ticket",
        toolset="tickets",
        description=(
            "Create a new ticket request. Prefer be_core request_type_code values like "
            "paid_leave, work_from_home, late_coming, early_leave, overtime_request, "
            "or others. Legacy aliases like sick_leave or overtime are normalized automatically."
        ),
        schema=CreateTicketInput,
        action_type="write",
        executor_class=TicketExecutor,
        executor_method="create_ticket",
    ),
    ToolMeta(
        name="approve_ticket",
        toolset="tickets",
        description="Approve a ticket workflow step.",
        schema=ApproveTicketInput,
        action_type="write",
        executor_class=TicketExecutor,
        executor_method="approve_ticket",
    ),
    # ── Documents ────────────────────────────────────────────────────────────
    ToolMeta(
        name="list_documents",
        toolset="documents",
        description="List documents in the organization filtered by folder, category, or project.",
        schema=ListDocumentsInput,
        action_type="read",
        executor_class=DocumentExecutor,
        executor_method="list_documents",
    ),
    ToolMeta(
        name="get_document_tree",
        toolset="documents",
        description="Get the full folder and document tree.",
        schema=GetDocumentTreeInput,
        action_type="read",
        executor_class=DocumentExecutor,
        executor_method="get_document_tree",
    ),
    # ── Organization ─────────────────────────────────────────────────────────
    ToolMeta(
        name="get_org_profile",
        toolset="organization",
        description="Get the current organization's profile.",
        schema=GetOrgProfileInput,
        action_type="read",
        executor_class=OrganizationExecutor,
        executor_method="get_org_profile",
    ),
    ToolMeta(
        name="list_org_members",
        toolset="organization",
        description="List members in the organization.",
        schema=ListOrgMembersInput,
        action_type="read",
        executor_class=OrganizationExecutor,
        executor_method="list_org_members",
    ),
]

TOOL_REGISTRY: dict[str, ToolMeta] = {t.name: t for t in _TOOL_DEFINITIONS}

READ_TOOLS: set[str] = {t.name for t in _TOOL_DEFINITIONS if t.action_type == "read"}
WRITE_TOOLS: set[str] = {t.name for t in _TOOL_DEFINITIONS if t.action_type == "write"}
TOOLSET_REGISTRY: dict[str, list[str]] = {}

for tool in _TOOL_DEFINITIONS:
    TOOLSET_REGISTRY.setdefault(tool.toolset, []).append(tool.name)


def get_tool_schema(tool_name: str) -> type[BaseModel] | None:
    meta = TOOL_REGISTRY.get(tool_name)
    return meta.schema if meta else None


def is_write_tool(tool_name: str) -> bool:
    return tool_name in WRITE_TOOLS


def get_tool_names_for_toolset(toolset: str) -> list[str]:
    normalized = (toolset or "auto").strip().lower()
    if normalized == "none":
        return []
    if normalized == "auto":
        return list(TOOL_REGISTRY.keys())
    return list(TOOLSET_REGISTRY.get(normalized, []))
