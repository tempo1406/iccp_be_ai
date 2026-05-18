from __future__ import annotations

from typing import Any, Optional

from ...http_core import BeCoreHttpCore


class DailyReportsApi(BeCoreHttpCore):
    @staticmethod
    def _auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def get_daily_report(
        cls, project_id: str, date: Optional[str] = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if date:
            params["date"] = date
        body = await cls._request(
            "GET", f"/api/v1/projects/{project_id}/daily-reports/me",
            params=params, headers=headers,
        )
        return cls._unwrap_api_response(body)

    @classmethod
    async def submit_daily_report(
        cls, project_id: str, report_id: str,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request(
            "POST", f"/api/v1/projects/{project_id}/daily-reports/{report_id}/submit",
            headers=headers,
        )
        return cls._unwrap_api_response(body)
