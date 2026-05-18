from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SendNotificationRequest:
    user_id: str
    title: str
    message: str
    notification_type: str = "info"
    data: dict[str, Any] = field(default_factory=dict)
