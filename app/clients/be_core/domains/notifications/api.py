from __future__ import annotations

import structlog

from ...http_core import BeCoreHttpCore
from .dto.request.send_notification_request import SendNotificationRequest
from .dto.response.send_notification_response import SendNotificationResponse

log = structlog.get_logger(__name__)


class BeCoreNotificationsApi(BeCoreHttpCore):
    @staticmethod
    def _build_auth_headers(bearer_token: str | None) -> dict[str, str] | None:
        if not bearer_token:
            return None
        token = bearer_token.strip()
        if not token:
            return None
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}

    @classmethod
    async def send_notification(
        cls,
        user_id: str,
        title: str,
        message: str,
        notification_type: str = "info",
        data: dict | None = None,
        bearer_token: str | None = None,
    ) -> SendNotificationResponse:
        request_dto = SendNotificationRequest(
            user_id=user_id,
            title=title,
            message=message,
            notification_type=notification_type,
            data=data or {},
        )
        payload = {
            "userId": request_dto.user_id,
            "title": request_dto.title,
            "message": request_dto.message,
            "type": request_dto.notification_type,
            "data": request_dto.data,
        }
        headers = cls._build_auth_headers(bearer_token)

        try:
            await cls._request(
                "POST",
                "/api/v1/notifications/internal",
                json=payload,
                headers=headers,
            )
            log.debug("be_core_client.notification_sent", user_id=user_id, title=title)
            return SendNotificationResponse(success=True)
        except Exception as exc:
            log.warning("be_core_client.notification_failed", user_id=user_id, error=str(exc))
            return SendNotificationResponse(success=False)
