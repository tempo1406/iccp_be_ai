from __future__ import annotations

from typing import Any, Optional

from ...http_core import BeCoreHttpCore


class TicketsApi(BeCoreHttpCore):
    @staticmethod
    def _auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def list_my_tickets(
        cls, status: Optional[str] = None, page: Optional[int] = None,
        limit: Optional[int] = None, bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if page:
            params["page"] = page
        if limit:
            params["limit"] = limit
        body = await cls._request("GET", "/api/v1/ticket-requests/me", params=params, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def get_ticket_detail(cls, ticket_id: str, bearer_token: str | None = None) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        body = await cls._request("GET", f"/api/v1/ticket-requests/{ticket_id}", headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def create_ticket(
        cls,
        request_type_code: str,
        title: str,
        content: str,
        request_type_name: Optional[str] = None,
        reason_code: Optional[str] = None,
        reason_detail: Optional[str] = None,
        delegate_id: Optional[str] = None,
        effort_owner_id: Optional[str] = None,
        cc_member_ids: Optional[list[str]] = None,
        start_at: Optional[str] = None,
        end_at: Optional[str] = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        payload: dict[str, Any] = {
            "requestTypeCode": request_type_code,
            "title": title,
            "content": content,
        }
        if request_type_name:
            payload["requestTypeName"] = request_type_name
        if reason_code:
            payload["reasonCode"] = reason_code
        if reason_detail:
            payload["reasonDetail"] = reason_detail
        if delegate_id:
            payload["delegateId"] = delegate_id
        if effort_owner_id:
            payload["effortOwnerId"] = effort_owner_id
        if cc_member_ids:
            payload["ccMemberIds"] = cc_member_ids
        if start_at:
            payload["startAt"] = start_at
        if end_at:
            payload["endAt"] = end_at
        body = await cls._request("POST", "/api/v1/ticket-requests", json=payload, headers=headers)
        return cls._unwrap_api_response(body)

    @classmethod
    async def approve_ticket(
        cls, ticket_id: str, comment: Optional[str] = None,
        bearer_token: str | None = None,
    ) -> dict[str, Any]:
        headers = cls._auth_headers(bearer_token)
        payload: dict[str, Any] = {}
        if comment:
            payload["comment"] = comment
        body = await cls._request(
            "POST", f"/api/v1/ticket-requests/{ticket_id}/approve",
            json=payload, headers=headers,
        )
        return cls._unwrap_api_response(body)
