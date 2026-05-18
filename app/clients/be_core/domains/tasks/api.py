from __future__ import annotations

from typing import Any, Optional

from ...http_core import BeCoreHttpCore


class TasksApi(BeCoreHttpCore):
    @staticmethod
    def _auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def list_tasks(
        cls, project_id: str, assignee_id: Optional[str] = None,
        due_state: Optional[str] = None, bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if assignee_id:
            params["assigneeId"] = assignee_id
        if due_state:
            params["dueState"] = due_state
        body = await cls._request("GET", f"/api/v1/projects/{project_id}/tasks", params=params, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def get_task_detail(cls, project_id: str, task_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request("GET", f"/api/v1/projects/{project_id}/tasks/{task_id}", headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def create_task(
        cls, project_id: str, title: str, description: Optional[str] = None,
        status_id: Optional[str] = None, assigned_to: Optional[str] = None,
        due_date: Optional[str] = None, priority: Optional[str] = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        payload: dict[str, Any] = {"title": title}
        if description:
            payload["description"] = description
        if status_id:
            payload["statusId"] = status_id
        if assigned_to:
            payload["assignedTo"] = assigned_to
        if due_date:
            payload["dueDate"] = due_date
        if priority:
            payload["priority"] = priority
        body = await cls._request("POST", f"/api/v1/projects/{project_id}/tasks", json=payload, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def update_task_status(
        cls, project_id: str, task_id: str, status_id: str,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request(
            "PUT", f"/api/v1/projects/{project_id}/tasks/{task_id}/status",
            json={"statusId": status_id}, headers=headers,
        )
        return cls._unwrap_api_response(body)

    @classmethod
    async def add_task_comment(
        cls, project_id: str, task_id: str, content: str,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request(
            "POST", f"/api/v1/projects/{project_id}/tasks/{task_id}/comments",
            json={"content": content}, headers=headers,
        )
        return cls._unwrap_api_response(body)
