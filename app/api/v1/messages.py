from __future__ import annotations

import asyncio
import json
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from app.agents.citation_utils import collapse_stored_citations
from app.agents.orchestrator import AgentOrchestrator, OrchestratorInput
from app.core.dependencies import CurrentUser, get_db
from app.db.mongodb import get_database
from app.db.repositories.conversation_repo import ConversationRepository
from app.db.repositories.message_repo import MessageRepository
from app.db.repositories.pending_action_repo import PendingActionRepository
from app.schemas.chat import (
    ConversationHistoryResponse,
    MessageResponse,
    CitationResponse,
    PendingActionResponse,
    SendMessageRequest,
)
from app.schemas.chat_runtime import (
    normalize_assistant_config,
    normalize_chat_toolset,
    resolve_chat_mode,
)
from app.schemas.common import ApiResponse, ErrorResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["Messages"])

_orchestrator = AgentOrchestrator()


def _message_to_response(msg) -> MessageResponse:
    citations = [
        CitationResponse(
            document_id=c.document_id,
            chunk_index=c.chunk_index,
            file_name=c.file_name,
            relevance_score=c.relevance_score,
            cited_content=c.cited_content,
            chunk_count=c.chunk_count,
        )
        for c in collapse_stored_citations(msg.citations or [])
    ]
    return MessageResponse(
        id=msg.id,
        conversation_id=msg.conversation_id,
        role=msg.role,
        content=msg.content,
        mode=msg.mode,
        intent=msg.intent,
        citations=citations,
        web_sources=msg.web_sources or [],
        model_name=msg.model_name,
        model_used=msg.model_used,
        model_config_id=msg.model_config_id,
        tokens_used=msg.tokens_used,
        documents_retrieved=msg.documents_retrieved,
        response_time_ms=msg.response_time_ms,
        created_at=msg.created_at,
    )


def _pending_action_to_response(action) -> PendingActionResponse:
    return PendingActionResponse(
        id=action.id,
        conversation_id=action.conversation_id,
        tool_name=action.tool_name,
        preview=action.preview,
        status="expired" if action.status == "pending" and action.is_expired() else action.status,
        error=action.error,
        created_at=action.created_at,
        expires_at=action.expires_at,
    )


@router.post(
    "/{conversation_id}/messages",
    summary="Send a message — Streaming SSE",
    description=(
        "Gửi tin nhắn và nhận phản hồi streaming qua **Server-Sent Events (SSE)**.\n\n"
        "### Modes\n"
        "- `general` — trợ lý mặc định, không ép tài liệu/tool\n"
        "- `auto` — tự dùng tài liệu nội bộ + web khi cần\n"
        "- `rag` — tìm kiếm trong tài liệu nội bộ (Pinecone)\n"
        "- `web` — tìm kiếm web (DuckDuckGo)\n\n"
        "Mode mặc định lấy từ conversation. Có thể override qua field `mode` trong request body.\n\n"
        "Ngoài ra có thể override `toolset` để giới hạn nhóm tool được phép gọi trong tin nhắn này.\n\n"
        "### Luồng xử lý theo mode\n"
        "1. `auto`: router -> chitchat hoặc retrieval nội bộ + web search -> generate -> stream token\n"
        "2. `rag`: router -> retrieval nội bộ -> generate -> stream token\n"
        "3. `web`: router -> web search -> generate -> stream token\n\n"
        "### SSE events\n"
        "- `token`: mảnh text trả dần\n"
        "- `tool_call`: trạng thái tool đang được gọi\n"
        "- `tool_result`: kết quả tool\n"
        "- `confirm_required`: cần user xác nhận trước khi thực thi write action\n"
        "- `done`: kết thúc, có `message_id` + `citations` + `web_sources`\n"
        "- `suggestions`: câu hỏi gợi ý sau phản hồi\n"
        "- `error`: lỗi runtime\n\n"
        "Lưu ý: Swagger UI chỉ hiển thị schema/response sample, không render realtime stream như FE client."
    ),
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "SSE stream (`text/event-stream`)",
            "content": {
                "text/event-stream": {
                    "example": (
                        "data: {\"type\":\"token\",\"content\":\"Theo\"}\n\n"
                        "data: {\"type\":\"token\",\"content\":\" chính sách\"}\n\n"
                        "data: {\"type\":\"done\",\"message_id\":\"msg_123\",\"citations\":[],\"web_sources\":[]}\n\n"
                        "data: {\"type\":\"suggestions\",\"questions\":[\"Câu hỏi tiếp theo?\"]}\n\n"
                    )
                }
            },
        },
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse, "description": "Conversation not found"},
        429: {"model": ErrorResponse, "description": "Quota exceeded"},
    },
)
async def send_message(
    conversation_id: str,
    body: Annotated[
        SendMessageRequest,
        Body(
            description=(
                "Payload gửi tin nhắn. Có thể truyền `model_config_id` để chọn model cụ thể; "
                "nếu bỏ trống, backend sẽ tự resolve theo purpose và subscription."
            ),
            openapi_examples={
                "rag_default": {
                    "summary": "Auto mode (mặc định từ conversation)",
                    "value": {
                        "content": "Quy trình đăng ký nghỉ phép năm của công ty là gì?",
                        "mode": "auto",
                        "toolset": "auto",
                        "model_config_id": None,
                    },
                },
                "web_mode": {
                    "summary": "Web mode",
                    "value": {
                        "content": "Tin công nghệ AI mới nhất hôm nay là gì?",
                        "mode": "web",
                        "toolset": "none",
                        "model_config_id": None,
                    },
                },
                "auto_with_selected_model": {
                    "summary": "Auto + chọn model cụ thể",
                    "value": {
                        "content": "So sánh chính sách nội bộ với best practice ngoài thị trường.",
                        "mode": "auto",
                        "toolset": "auto",
                        "model_config_id": "8d2f0e5c-bf9a-4e14-b1fd-9e0b8a2a8d60",
                    },
                },
            },
        ),
    ],
    user: CurrentUser,
    db: Annotated[Any, Depends(get_db)],
    request: Request,
) -> StreamingResponse:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    # Verify conversation exists and belongs to user
    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_by_id(conversation_id)

    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conv.user_id != user.user_id or conv.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    # Determine runtime config: body override > conversation default
    conv_metadata = conv.metadata or {}
    effective_assistant_config = normalize_assistant_config(
        body.assistant_config.model_dump() if body.assistant_config else conv_metadata.get("assistant_config"),
        body.mode or conv.mode,
        body.toolset or conv.toolset,
    )
    effective_mode = resolve_chat_mode(
        body.mode or conv.mode,
        body.toolset or conv.toolset,
        effective_assistant_config,
    )
    effective_toolset = normalize_chat_toolset(
        (body.toolset or conv.toolset)
        if effective_assistant_config["internal_enabled"]
        else "none"
    )
    effective_context_scope = body.context_scope or conv_metadata.get("context_scope", "organization")
    effective_context_id = (
        body.context_id
        if body.context_scope is not None or body.context_id is not None
        else conv_metadata.get("context_id")
    )
    effective_context_options = (
        body.context_options
        if body.context_options is not None
        else conv_metadata.get("context_options") or {}
    )

    trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4()))
    log.info(
        "messages.send_message.requested",
        trace_id=trace_id,
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
        requested_mode=body.mode,
        effective_mode=effective_mode,
        requested_toolset=body.toolset,
        effective_toolset=effective_toolset,
        effective_assistant_config=effective_assistant_config,
        context_scope=effective_context_scope,
        context_id=effective_context_id,
        context_options=effective_context_options,
        has_authorization=bool(request.headers.get("authorization")),
    )
    log.info(
        "messages.send_message.conversation_defaults",
        trace_id=trace_id,
        conversation_id=conversation_id,
        stored_mode=conv.mode,
        stored_toolset=conv.toolset,
        status=conv.status,
    )

    orchestrator_input = OrchestratorInput(
        organization_id=user.organization_id,
        user_id=user.user_id,
        conversation_id=conversation_id,
        user_message=body.content,
        mode=effective_mode,
        toolset=effective_toolset,
        assistant_config=effective_assistant_config,
        context_scope=effective_context_scope,
        context_id=effective_context_id,
        context_options=effective_context_options,
        model_config_id=str(body.model_config_id) if body.model_config_id else None,
        role_ids=user.role_ids or [],
        project_ids=user.project_ids or [],
        bearer_token=request.headers.get("authorization"),
        trace_id=trace_id,
        confirmed_action_id=body.confirmed_action_id,
        inline_history=body.inline_history,
    )

    async def event_generator():
        token_count = 0
        log.info(
            "messages.send_message.stream_opened",
            trace_id=trace_id,
            conversation_id=conversation_id,
            effective_mode=effective_mode,
            effective_toolset=effective_toolset,
        )
        async for event in _orchestrator.stream(orchestrator_input):
            if event.get("type") == "token":
                token_count += 1
            elif event.get("type") == "tool_call":
                log.info(
                    "messages.send_message.tool_call",
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    stream_token_events=token_count,
                    content_preview=str(event.get("content", ""))[:160],
                )
            elif event.get("type") == "tool_result":
                tool_data = event.get("data") or {}
                log.info(
                    "messages.send_message.tool_result",
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    tool_name=tool_data.get("tool"),
                    success=tool_data.get("success"),
                    has_error=bool(tool_data.get("error")),
                    stream_token_events=token_count,
                )
            elif event.get("type") == "confirm_required":
                log.info(
                    "messages.send_message.confirm_required",
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    action_id=event.get("action_id"),
                    tool_name=event.get("tool_name"),
                    stream_token_events=token_count,
                )
            elif event.get("type") == "suggestions":
                log.info(
                    "messages.send_message.suggestions",
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    suggestions_count=len(event.get("questions", [])),
                    stream_token_events=token_count,
                )
            elif event.get("type") == "done":
                log.info(
                    "messages.send_message.done",
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    response_message_id=event.get("message_id"),
                    citations_count=len(event.get("citations", [])),
                    web_sources_count=len(event.get("web_sources", [])),
                    response_time_ms=event.get("response_time_ms"),
                    tokens_used=event.get("tokens_used"),
                    stream_token_events=token_count,
                )
            elif event.get("type") == "error":
                log.error(
                    "messages.send_message.error",
                    trace_id=trace_id,
                    conversation_id=conversation_id,
                    error=event.get("error"),
                    stream_token_events=token_count,
                )

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            # Yield control to the event loop so Starlette flushes this chunk
            # to the client immediately instead of batching with the next token.
            await asyncio.sleep(0)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/{conversation_id}/actions/{action_id}/cancel",
    response_model=ApiResponse,
    summary="Cancel a pending action",
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def cancel_pending_action(
    conversation_id: str,
    action_id: str,
    user: CurrentUser,
) -> ApiResponse:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    db = get_database()
    repo = PendingActionRepository(db)
    pending = await repo.get_by_id(action_id)

    if not pending:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action not found")

    if pending.user_id != user.user_id or pending.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if pending.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not pending.is_pending():
        if pending.status == "pending" and pending.is_expired():
            await repo.update_status(action_id, "expired")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Action is already {pending.status}",
        )

    await repo.update_status(action_id, "cancelled")
    return ApiResponse(statusCode=200, message="Action cancelled successfully")


@router.get(
    "/{conversation_id}/actions",
    response_model=ApiResponse[list[PendingActionResponse]],
    summary="List pending/tool actions for a conversation",
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def list_pending_actions(
    conversation_id: str,
    user: CurrentUser,
    db: Annotated[Any, Depends(get_db)],
    status_filter: str | None = Query(default=None, alias="status"),
) -> ApiResponse[list[PendingActionResponse]]:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_by_id(conversation_id)
    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    if conv.user_id != user.user_id or conv.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    repo = PendingActionRepository(db)
    actions = await repo.get_by_conversation(conversation_id, status_filter)
    visible_actions = [
        action
        for action in actions
        if action.user_id == user.user_id and action.organization_id == user.organization_id
    ]
    return ApiResponse(
        statusCode=200,
        message="success",
        data=[_pending_action_to_response(action) for action in visible_actions],
    )


@router.get(
    "/{conversation_id}/messages",
    response_model=ApiResponse[ConversationHistoryResponse],
    summary="Get message history",
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
)
async def get_messages(
    conversation_id: str,
    user: CurrentUser,
    db: Annotated[Any, Depends(get_db)],
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> ApiResponse[ConversationHistoryResponse]:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    # Verify conversation access
    conv_repo = ConversationRepository(db)
    conv = await conv_repo.get_by_id(conversation_id)

    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conv.user_id != user.user_id or conv.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    msg_repo = MessageRepository(db)
    messages = await msg_repo.get_by_conversation(conversation_id, limit=limit, offset=offset)
    total = await msg_repo.count_by_conversation(conversation_id)

    return ApiResponse(
        statusCode=200,
        message="success",
        data=ConversationHistoryResponse(
            conversation_id=conversation_id,
            messages=[_message_to_response(m) for m in messages],
            total=total,
            limit=limit,
            offset=offset,
        ),
    )
