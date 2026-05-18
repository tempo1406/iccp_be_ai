from __future__ import annotations

from app.core.exceptions import UnauthorizedException

from ...http_core import BeCoreHttpCore
from .dto.request.introspect_token_request import IntrospectTokenRequest
from .dto.response.introspect_token_response import IntrospectTokenResponse


class BeCoreAuthApi(BeCoreHttpCore):
    @classmethod
    async def introspect_token(cls, token: str) -> dict:
        request_dto = IntrospectTokenRequest(token=token)
        try:
            body = await cls._request(
                "POST",
                "/api/v1/auth/introspect",
                headers={"Authorization": f"Bearer {request_dto.token}"},
            )
            response_dto = IntrospectTokenResponse(data=body.get("data") or body)
            return response_dto.data
        except Exception as exc:
            raise UnauthorizedException(f"Token introspection request failed: {exc}") from exc
