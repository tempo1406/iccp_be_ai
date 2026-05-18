from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendNotificationResponse:
    success: bool = True
