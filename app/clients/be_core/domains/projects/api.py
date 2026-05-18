from __future__ import annotations

from typing import Any, Optional

from ...http_core import BeCoreHttpCore


class ProjectsApi(BeCoreHttpCore):
    @staticmethod
    def _auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def list_projects(
        cls, page: Optional[int] = None, limit: Optional[int] = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if page:
            params["page"] = page
        if limit:
            params["limit"] = limit
        body = await cls._request("GET", "/api/v1/projects", params=params, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def get_project_detail(cls, project_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request("GET", f"/api/v1/projects/{project_id}", headers=headers)
        return cls._unwrap_api_response(body)
