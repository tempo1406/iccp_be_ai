from __future__ import annotations

import logging
from typing import Optional

from app.workers.celery_app import celery_app

log = logging.getLogger(__name__)


@celery_app.task(
    name="app.workers.tasks.analytics_tasks.record_analytics_task",
    ignore_result=True,
    queue="analytics",
)
def record_analytics_task(
    organization_id: str,
    user_id: str,
    conversation_id: str,
    message_id: str,
    query_text: str,
    response_time_ms: int,
    tokens_used: int,
    documents_retrieved: int,
    feedback_rating: Optional[int] = None,
) -> None:
    """Fire-and-forget Celery task to log analytics events."""
    log.info(
        "[analytics_task] chat_event "
        f"org={organization_id} user={user_id} "
        f"conv={conversation_id} msg={message_id} "
        f"response_ms={response_time_ms} tokens={tokens_used} "
        f"docs={documents_retrieved} rating={feedback_rating}"
    )
