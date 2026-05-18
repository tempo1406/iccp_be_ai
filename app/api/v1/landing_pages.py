from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse

from app.agents.landing_page_agent import LandingPageAgent
from app.core.dependencies import CurrentUser
from app.prompts.landing_page import resolve_generation_mode
from app.schemas.landing_page import GenerateLandingPageRequest
from app.services.quota_service import QuotaService

log = structlog.get_logger(__name__)

router = APIRouter(tags=["Landing Pages"])

_agent = LandingPageAgent()


@router.post(
    "/landing-pages/generate",
    summary="Generate or modify a landing page - Streaming SSE",
    description=(
        "Streams an HTML landing page token-by-token as Server-Sent Events.\n\n"
        "Event types:\n"
        "- `chunk` - partial HTML token\n"
        "- `done` - generation complete\n"
        "- `error` - error message\n\n"
        "Pass `conversation` to continue the editor conversation across turns.\n"
        "`mode` is optional; if omitted, the backend infers generate vs modify from `current_html`.\n"
        "Pass `current_html` to provide the latest canvas state as context.\n"
        "Pass `custom_api_key` to override the Gemini fallback key when Gemini is selected "
        "(optional, never persisted)."
    ),
)
async def generate_landing_page(
    body: GenerateLandingPageRequest,
    current_user: CurrentUser,
) -> StreamingResponse:
    if not current_user.organization_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="organization_id required",
        )

    await QuotaService.check_and_increment(
        current_user.user_id,
        current_user.organization_id,
    )

    effective_mode = resolve_generation_mode(body.mode, body.current_html)

    log.info(
        "landing_page.generate",
        user_id=current_user.user_id,
        org_slug=body.org_context.slug,
        mode=effective_mode,
        modify=effective_mode == "modify",
        conversation_turns=len(body.conversation),
        custom_key=bool(body.custom_api_key),
    )

    async def event_stream():
        async for chunk in _agent.stream(
            body.org_context,
            body.conversation,
            body.mode,
            body.user_prompt,
            body.current_html,
            body.custom_api_key,
            user_id=current_user.user_id,
            organization_id=current_user.organization_id,
        ):
            yield chunk
            await asyncio.sleep(0)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
