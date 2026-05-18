from __future__ import annotations

import asyncio
from typing import Any

import structlog

from app.clients.be_core_client import BeCoreClient

from ..base import ToolContext
from ..schemas.tasks import (
    AddTaskCommentInput,
    CreateTaskInput,
    GetTaskDetailInput,
    ListTasksInput,
    UpdateTaskStatusInput,
)

log = structlog.get_logger(__name__)

# Max concurrent project fetches to avoid overwhelming be_core
_MAX_CONCURRENT_PROJECT_FETCHES = 5


class TaskExecutor:
    @staticmethod
    async def list_tasks(input: ListTasksInput, ctx: ToolContext) -> dict[str, Any]:
        # Normalize assignee_id: 'me' or empty means current user
        assignee_id = input.assignee_id
        if not assignee_id or assignee_id.lower() in ("me", "tôi", "mình"):
            assignee_id = ctx.user_id

        # If project_id provided, list tasks in that project only
        if input.project_id:
            return await BeCoreClient.list_tasks(
                project_id=input.project_id,
                assignee_id=assignee_id,
                due_state=input.due_state,
                bearer_token=ctx.bearer_token,
            )

        # Cross-project: list all user's projects, then fetch tasks from each concurrently
        log.info("task_executor.cross_project_list", user_id=ctx.user_id)
        projects_resp = await BeCoreClient.list_projects(
            page=1, limit=100, bearer_token=ctx.bearer_token
        )
        projects = projects_resp.get("items", []) if isinstance(projects_resp, dict) else []
        if not projects:
            return {"items": [], "total": 0, "message": "Bạn chưa tham gia dự án nào."}

        async def _fetch_project_tasks(proj: dict) -> list[dict]:
            """Fetch tasks for a single project with error handling."""
            proj_id = proj.get("id")
            proj_name = proj.get("name", "")
            if not proj_id:
                return []
            try:
                task_resp = await BeCoreClient.list_tasks(
                    project_id=proj_id,
                    assignee_id=assignee_id,
                    due_state=input.due_state,
                    bearer_token=ctx.bearer_token,
                )
                tasks = task_resp.get("items", []) if isinstance(task_resp, dict) else []
                # Inject project name into each task for context
                for t in tasks:
                    if isinstance(t, dict):
                        t["_project_name"] = proj_name
                return tasks
            except Exception as exc:
                log.warning(
                    "task_executor.project_fetch_failed",
                    project_id=proj_id,
                    error=str(exc),
                    trace_id=ctx.trace_id,
                )
                return []

        # Fetch tasks concurrently with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PROJECT_FETCHES)

        async def _fetch_with_limit(proj: dict) -> list[dict]:
            async with semaphore:
                return await _fetch_project_tasks(proj)

        results = await asyncio.gather(*[_fetch_with_limit(proj) for proj in projects])
        all_tasks: list[dict] = []
        for tasks in results:
            all_tasks.extend(tasks)

        return {
            "items": all_tasks,
            "total": len(all_tasks),
            "projects_searched": len(projects),
        }

    @staticmethod
    async def get_task_detail(input: GetTaskDetailInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.get_task_detail(
            project_id=input.project_id,
            task_id=input.task_id,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def create_task(input: CreateTaskInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.create_task(
            project_id=input.project_id,
            title=input.title,
            description=input.description,
            status_id=input.status_id,
            assigned_to=input.assigned_to,
            due_date=input.due_date,
            priority=input.priority,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def update_task_status(input: UpdateTaskStatusInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.update_task_status(
            project_id=input.project_id,
            task_id=input.task_id,
            status_id=input.status_id,
            bearer_token=ctx.bearer_token,
        )

    @staticmethod
    async def add_task_comment(input: AddTaskCommentInput, ctx: ToolContext) -> dict[str, Any]:
        return await BeCoreClient.add_task_comment(
            project_id=input.project_id,
            task_id=input.task_id,
            content=input.content,
            bearer_token=ctx.bearer_token,
        )
