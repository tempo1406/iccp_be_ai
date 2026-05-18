from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query, status

from app.core.dependencies import CurrentUser, DBSession
from app.db.repositories.conversation_repo import ConversationRepository
from app.schemas.chat_runtime import (
    normalize_assistant_config,
    normalize_chat_toolset,
    resolve_chat_mode,
    search_mode_from_mode,
)
from app.schemas.chat import (
    ConversationResponse,
    CreateConversationRequest,
    UpdateConversationRequest,
)
from app.schemas.common import ApiResponse, ErrorResponse

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["Conversations"])

_COMMON_RESPONSES: dict = {
    401: {"model": ErrorResponse, "description": "Unauthorized"},
    403: {"model": ErrorResponse, "description": "Missing organization_id"},
}


def _conversation_to_response(conv) -> ConversationResponse:
    metadata = conv.metadata or {}
    assistant_config = normalize_assistant_config(
        metadata.get("assistant_config"),
        conv.mode,
        conv.toolset,
    )
    return ConversationResponse(
        id=conv.id,
        organization_id=conv.organization_id,
        user_id=conv.user_id,
        title=conv.title,
        mode=conv.mode,
        toolset=conv.toolset,
        search_mode=metadata.get("search_mode") or search_mode_from_mode(conv.mode),
        assistant_config=assistant_config,
        status=conv.status,
        context_scope=metadata.get("context_scope", "organization"),
        context_id=metadata.get("context_id"),
        context_options=metadata.get("context_options") or {},
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.post(
    "",
    response_model=ApiResponse[ConversationResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new conversation",
    responses={201: {"description": "Conversation created"}, **_COMMON_RESPONSES},
)
async def create_conversation(
    body: CreateConversationRequest,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[ConversationResponse]:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    assistant_config = normalize_assistant_config(
        body.assistant_config.model_dump() if body.assistant_config else None,
        body.mode.value,
        body.toolset.value,
    )
    resolved_mode = resolve_chat_mode(body.mode.value, body.toolset.value, assistant_config)
    resolved_toolset = normalize_chat_toolset(
        body.toolset.value if assistant_config["internal_enabled"] else "none"
    )

    log.info(
        "conversations.create_requested",
        organization_id=user.organization_id,
        user_id=user.user_id,
        mode=resolved_mode,
        toolset=resolved_toolset,
        assistant_config=assistant_config,
        context_scope=body.context_scope,
        context_id=body.context_id,
        has_context_options=bool(body.context_options),
    )

    repo = ConversationRepository(db)
    conv = await repo.create(
        org_id=user.organization_id,
        user_id=user.user_id,
        title=body.title,
        mode=resolved_mode,
        toolset=resolved_toolset,
    )

    # Store context info in metadata
    updated = await repo.update(conv.id, {
        "metadata": {
            "context_scope": body.context_scope,
            "context_id": body.context_id,
            "context_options": body.context_options or {},
            "search_mode": body.search_mode or search_mode_from_mode(resolved_mode),
            "assistant_config": assistant_config,
        }
    })
    if updated:
        conv = updated

    log.info(
        "conversations.created",
        conversation_id=conv.id,
        organization_id=user.organization_id,
        user_id=user.user_id,
        mode=resolved_mode,
        toolset=resolved_toolset,
        context_scope=body.context_scope,
        context_id=body.context_id,
    )
    return ApiResponse(statusCode=201, message="Conversation created", data=_conversation_to_response(conv))


@router.get(
    "",
    response_model=ApiResponse[list[ConversationResponse]],
    summary="List user's conversations",
    responses=_COMMON_RESPONSES,
)
async def list_conversations(
    user: CurrentUser,
    db: DBSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status_filter: str | None = Query(default=None, alias="status"),
) -> ApiResponse[list[ConversationResponse]]:
    if not user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="organization_id required")

    repo = ConversationRepository(db)
    conversations = await repo.get_by_user(
        user_id=user.user_id,
        org_id=user.organization_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    log.info(
        "conversations.listed",
        organization_id=user.organization_id,
        user_id=user.user_id,
        status_filter=status_filter,
        limit=limit,
        offset=offset,
        conversations_count=len(conversations),
    )
    return ApiResponse(
        statusCode=200,
        message="success",
        data=[_conversation_to_response(c) for c in conversations],
    )


@router.get(
    "/{conversation_id}",
    response_model=ApiResponse[ConversationResponse],
    summary="Get single conversation",
    responses={404: {"model": ErrorResponse}, **_COMMON_RESPONSES},
)
async def get_conversation(
    conversation_id: str,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[ConversationResponse]:
    repo = ConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)

    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    # Ensure user can only access their own conversations
    if conv.user_id != user.user_id or conv.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    log.info(
        "conversations.fetched",
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
        mode=conv.mode,
        toolset=conv.toolset,
        status=conv.status,
    )
    return ApiResponse(statusCode=200, message="success", data=_conversation_to_response(conv))


@router.put(
    "/{conversation_id}",
    response_model=ApiResponse[ConversationResponse],
    summary="Update conversation title or mode",
    responses={404: {"model": ErrorResponse}, **_COMMON_RESPONSES},
)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[ConversationResponse]:
    repo = ConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)

    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conv.user_id != user.user_id or conv.organization_id != user.organization_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    log.info(
        "conversations.update_requested",
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
        current_mode=conv.mode,
        current_toolset=conv.toolset,
        requested_title=body.title,
        requested_mode=body.mode.value if body.mode is not None else None,
        requested_toolset=body.toolset.value if body.toolset is not None else None,
        requested_assistant_config=body.assistant_config.model_dump() if body.assistant_config is not None else None,
        requested_search_mode=body.search_mode,
        requested_context_scope=body.context_scope,
        requested_context_id=body.context_id,
    )

    metadata = dict(conv.metadata or {})
    mode_source = body.mode.value if body.mode is not None else conv.mode
    toolset_source = body.toolset.value if body.toolset is not None else conv.toolset
    assistant_config = normalize_assistant_config(
        body.assistant_config.model_dump() if body.assistant_config is not None else metadata.get("assistant_config"),
        mode_source,
        toolset_source,
    )
    resolved_mode = resolve_chat_mode(mode_source, toolset_source, assistant_config)
    resolved_toolset = normalize_chat_toolset(
        toolset_source if assistant_config["internal_enabled"] else "none"
    )

    update_data: dict = {}
    if body.title is not None:
        update_data["title"] = body.title
    if body.mode is not None or body.toolset is not None or body.assistant_config is not None:
        update_data["mode"] = resolved_mode
        update_data["toolset"] = resolved_toolset
    if (
        body.search_mode is not None
        or body.context_scope is not None
        or body.context_id is not None
        or body.context_options is not None
        or body.assistant_config is not None
        or body.mode is not None
        or body.toolset is not None
    ):
        if body.context_scope is not None:
            metadata["context_scope"] = body.context_scope
        if body.context_id is not None or body.context_scope is not None:
            metadata["context_id"] = body.context_id
        if body.context_options is not None:
            metadata["context_options"] = body.context_options
        if body.search_mode is not None:
            metadata["search_mode"] = body.search_mode
        elif body.mode is not None or body.toolset is not None or body.assistant_config is not None:
            metadata["search_mode"] = search_mode_from_mode(resolved_mode)
        metadata["assistant_config"] = assistant_config
        update_data["metadata"] = metadata

    if not update_data:
        return ApiResponse(statusCode=200, message="success", data=_conversation_to_response(conv))

    updated = await repo.update(conversation_id, update_data)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    log.info(
        "conversations.updated",
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
        changed_fields=sorted(update_data.keys()),
        mode=updated.mode,
        toolset=updated.toolset,
    )
    return ApiResponse(statusCode=200, message="success", data=_conversation_to_response(updated))


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_200_OK,
    response_model=ApiResponse,
    summary="Soft delete conversation",
    responses={404: {"model": ErrorResponse}, **_COMMON_RESPONSES},
)
async def delete_conversation(
    conversation_id: str,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse:
    repo = ConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)

    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conv.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    deleted = await repo.soft_delete(conversation_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    log.info(
        "conversations.deleted",
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
    )
    return ApiResponse(statusCode=200, message="Conversation deleted successfully")


@router.put(
    "/{conversation_id}/archive",
    response_model=ApiResponse[ConversationResponse],
    summary="Archive conversation",
    responses={404: {"model": ErrorResponse}, **_COMMON_RESPONSES},
)
async def archive_conversation(
    conversation_id: str,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[ConversationResponse]:
    repo = ConversationRepository(db)
    conv = await repo.get_by_id(conversation_id)

    if not conv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if conv.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    archived = await repo.archive(conversation_id)
    if not archived:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    updated = await repo.get_by_id(conversation_id)
    log.info(
        "conversations.archived",
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
    )
    return ApiResponse(statusCode=200, message="success", data=_conversation_to_response(updated))


@router.put(
    "/{conversation_id}/restore",
    response_model=ApiResponse[ConversationResponse],
    summary="Restore archived conversation",
    responses={404: {"model": ErrorResponse}, **_COMMON_RESPONSES},
)
async def restore_conversation(
    conversation_id: str,
    user: CurrentUser,
    db: DBSession,
) -> ApiResponse[ConversationResponse]:
    repo = ConversationRepository(db)

    # Need to fetch including archived ones — get_by_id excludes deleted_at but not archived
    doc = await db["conversations"].find_one({"_id": conversation_id, "deleted_at": None})
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    from app.db.schemas.conversation import ConversationSchema
    conv = ConversationSchema(**{k: v for k, v in doc.items() if k != "_id"})

    if conv.user_id != user.user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    restored = await repo.restore(conversation_id)
    if not restored:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Conversation is not archived")

    updated = await repo.get_by_id(conversation_id)
    log.info(
        "conversations.restored",
        conversation_id=conversation_id,
        organization_id=user.organization_id,
        user_id=user.user_id,
    )
    return ApiResponse(statusCode=200, message="success", data=_conversation_to_response(updated))
