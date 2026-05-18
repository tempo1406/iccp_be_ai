from __future__ import annotations

from typing import Any, Optional

from ...http_core import BeCoreHttpCore


class OrganizationsApi(BeCoreHttpCore):
    @staticmethod
    def _auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def get_org_profile(cls, bearer_token: str | None = None) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request("GET", "/api/v1/organizations/profile", headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def list_org_members(
        cls, page: Optional[int] = None, limit: Optional[int] = None,
        search: Optional[str] = None, bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if page:
            params["page"] = page
        if limit:
            params["limit"] = limit
        if search:
            params["search"] = search
        body = await cls._request("GET", "/api/v1/organizations/manage/members", params=params, headers=headers)
        return cls._unwrap_api_response(body)
