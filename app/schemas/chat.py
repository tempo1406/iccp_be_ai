from datetime import datetime
from uuid import UUID
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.chat_runtime import (
    ChatContextScope,
    ChatExternalMode,
    ChatMode,
    ChatSearchMode,
    ChatToolset,
    normalize_assistant_config,
    normalize_chat_mode,
    normalize_chat_toolset,
    resolve_chat_mode,
    search_mode_from_mode,
    uses_internal_docs,
)


class ChatAssistantConfigPayload(BaseModel):
    internal_enabled: bool = Field(..., description="Bật khả năng dùng tài liệu/tool nội bộ")
    external_enabled: bool = Field(..., description="Bật khả năng dùng web/tool bên ngoài")
    external_mode: ChatExternalMode = Field(
        default="web_search",
        description="Chế độ external hiện hỗ trợ",
    )


class CreateConversationRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "title": "Hỏi về chính sách nghỉ phép",
                "mode": "general",
                "toolset": "none",
                "search_mode": "hybrid",
                "context_scope": "organization",
                "context_id": None,
                "context_options": {
                    "strict_scope": False,
                    "include_subfolders": False,
                },
            }
        }
    )

    title: Optional[str] = Field(None, description="Tiêu đề cuộc trò chuyện (tùy chọn)")
    mode: ChatMode = Field(
        default=ChatMode.GENERAL,
        description="Chế độ chat: `general` (mặc định, không ép RAG/tool) | `auto` | `rag` | `web`",
        examples=["general", "auto", "rag", "web"],
    )
    toolset: ChatToolset = Field(
        default=ChatToolset.NONE,
        description=(
            "Nhóm tool được phép dùng trong phiên chat: `none` (mặc định, không dùng tool) | `auto` (toàn bộ) | "
            "`projects` | `tasks` | `tickets` | `documents` | `organization` | `daily_reports`"
        ),
        examples=["none", "auto", "tasks", "tickets"],
    )
    search_mode: Optional[ChatSearchMode] = Field(
        None,
        description=(
            "Trường tương thích cũ cho FE. Backend ưu tiên `mode`; nếu thiếu thì sẽ tự suy ra "
            "`rag_internal` / `hybrid` / `web_only`."
        ),
    )
    assistant_config: Optional[ChatAssistantConfigPayload] = Field(
        default=None,
        description="Cấu hình capability mới cho FE: bật/tắt Internal Tool và External Tool.",
    )
    context_scope: ChatContextScope = Field(
        default="organization",
        description=(
            "Phạm vi tài liệu: `organization` | `project` | `my_docs` | `folder` | `document` | `custom_docs`"
        ),
    )
    context_id: Optional[str] = Field(
        None,
        description="ID theo scope (project/folder/document).",
    )
    context_options: Optional[dict] = Field(
        default=None,
        description=(
            "Tùy chọn bổ sung theo scope, ví dụ: strict_scope, include_subfolders, "
            "prefer_owner, include_shared_docs, time_range, document_ids"
        ),
    )

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, value: object) -> object:
        return normalize_chat_mode(value if isinstance(value, (str, ChatMode)) else None)

    @field_validator("toolset", mode="before")
    @classmethod
    def normalize_toolset(cls, value: object) -> object:
        return normalize_chat_toolset(
            value if isinstance(value, (str, ChatToolset)) else None
        )

    @model_validator(mode="after")
    def validate_scope_fields(self) -> "CreateConversationRequest":
        normalized_assistant = normalize_assistant_config(
            self.assistant_config.model_dump() if self.assistant_config else None,
            self.mode,
            self.toolset,
        )
        resolved_mode = resolve_chat_mode(self.mode, self.toolset, normalized_assistant)
        self.assistant_config = ChatAssistantConfigPayload(**normalized_assistant)
        self.mode = ChatMode(resolved_mode)
        if not self.assistant_config.internal_enabled:
            self.toolset = ChatToolset.NONE

        scope_requires_id = {"project", "folder", "document"}
        if uses_internal_docs(self.mode) and self.context_scope in scope_requires_id and not self.context_id:
            raise ValueError(f"context_id is required when context_scope={self.context_scope}")
        if uses_internal_docs(self.mode) and self.context_scope == "custom_docs":
            document_ids = (self.context_options or {}).get("document_ids") or []
            if not isinstance(document_ids, list) or not document_ids:
                raise ValueError("context_options.document_ids is required when context_scope=custom_docs")
        if self.context_scope not in scope_requires_id:
            self.context_id = None
        if self.search_mode is None:
            self.search_mode = search_mode_from_mode(self.mode)
        return self


class UpdateConversationRequest(BaseModel):
    title: Optional[str] = Field(None, description="Tiêu đề mới")
    mode: Optional[ChatMode] = Field(
        None,
        description="Chế độ chat mới",
        examples=["auto", "rag", "web"],
    )
    toolset: Optional[ChatToolset] = Field(
        None,
        description="Nhóm tool mới cho conversation",
        examples=["auto", "none", "tasks", "tickets"],
    )
    search_mode: Optional[ChatSearchMode] = Field(
        None,
        description="Search mode tương thích FE: rag_internal | hybrid | web_only",
    )
    assistant_config: Optional[ChatAssistantConfigPayload] = Field(
        default=None,
        description="Cấu hình capability mới: bật/tắt Internal Tool và External Tool",
    )
    context_scope: Optional[ChatContextScope] = Field(
        None,
        description="Phạm vi tài liệu mới cho conversation",
    )
    context_id: Optional[str] = Field(None, description="ID theo scope (project/folder/document)")
    context_options: Optional[dict[str, Any]] = Field(
        None,
        description="Tùy chọn scope, bao gồm document_ids cho custom_docs",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_update_mode(cls, value: object) -> object:
        if value is None:
            return None
        return normalize_chat_mode(value if isinstance(value, (str, ChatMode)) else None)

    @field_validator("toolset", mode="before")
    @classmethod
    def normalize_update_toolset(cls, value: object) -> object:
        if value is None:
            return None
        return normalize_chat_toolset(
            value if isinstance(value, (str, ChatToolset)) else None
        )

    @model_validator(mode="after")
    def validate_update_scope_fields(self) -> "UpdateConversationRequest":
        if self.assistant_config is not None:
            self.assistant_config = ChatAssistantConfigPayload(
                **normalize_assistant_config(
                    self.assistant_config.model_dump(),
                    self.mode,
                    self.toolset,
                )
            )
            if not self.assistant_config.internal_enabled:
                self.toolset = ChatToolset.NONE if self.toolset is not None else self.toolset

        if self.context_scope == "custom_docs":
            document_ids = (self.context_options or {}).get("document_ids") or []
            if not isinstance(document_ids, list) or not document_ids:
                raise ValueError("context_options.document_ids is required when context_scope=custom_docs")
            self.context_id = None
        elif self.context_scope in {"project", "folder", "document"} and not self.context_id:
            raise ValueError(f"context_id is required when context_scope={self.context_scope}")
        elif self.context_scope and self.context_scope not in {"project", "folder", "document"}:
            self.context_id = None
        if self.search_mode is None and self.mode is not None:
            self.search_mode = search_mode_from_mode(self.mode)
        return self


class ConversationResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "c1d2e3f4-1234-5678-abcd-ef0123456789",
                "organization_id": "a3bb189e-8bf9-3888-9912-ace4e6543002",
                "user_id": "u1b2c3d4-0000-1111-2222-333344445555",
                "title": "Hỏi về chính sách nghỉ phép",
                "mode": "general",
                "toolset": "none",
                "search_mode": "hybrid",
                "status": "active",
                "context_scope": "organization",
                "context_id": None,
                "context_options": {},
                "created_at": "2024-01-15T09:00:00Z",
                "updated_at": "2024-01-15T09:00:00Z",
            }
        }
    )

    id: str
    organization_id: str
    user_id: str
    title: Optional[str] = None
    mode: str = ChatMode.GENERAL.value
    toolset: str = ChatToolset.NONE.value
    search_mode: Optional[str] = None
    assistant_config: ChatAssistantConfigPayload
    status: str = "active"
    context_scope: str = "organization"
    context_id: Optional[str] = None
    context_options: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ImageAttachmentRequest(BaseModel):
    """
    Ảnh user gửi kèm tin nhắn.
    FE upload ảnh lên ImageKit trước, sau đó gửi URL vào đây.
    Endpoint: POST /conversations/{id}/messages/upload-image → trả về url
    """
    url: str = Field(..., description="URL ảnh trên ImageKit sau khi FE upload")
    mime_type: str = Field(
        default="image/jpeg",
        description="MIME type: image/jpeg | image/png | image/webp",
    )
    original_name: Optional[str] = Field(None, description="Tên file gốc (hiển thị trong history)")


class SendMessageRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "content": "Quy trình đăng ký nghỉ phép năm của công ty là gì?",
                    "mode": "general",
                    "toolset": "none",
                    "model_config_id": None,
                    "image": None,
                },
                {
                    "content": "Ảnh này nói về vấn đề gì?",
                    "mode": "rag",
                    "toolset": "none",
                    "image": {
                        "url": "https://ik.imagekit.io/org/chat/img123.jpg",
                        "mime_type": "image/jpeg",
                        "original_name": "question.jpg",
                    },
                },
            ]
        }
    )

    content: str = Field(
        ...,
        min_length=1,
        max_length=4096,
        description="Nội dung tin nhắn (tối đa 4096 ký tự)",
    )
    mode: Optional[ChatMode] = Field(
        None,
        description="Override chế độ chat cho tin nhắn này (mặc định dùng mode của conversation)",
        examples=["auto", "rag", "web"],
    )
    toolset: Optional[ChatToolset] = Field(
        None,
        description="Override nhóm tool cho tin nhắn này (mặc định dùng toolset của conversation)",
        examples=["auto", "none", "tasks", "tickets"],
    )
    model_config_id: Optional[UUID] = Field(
        None,
        description=(
            "ID cấu hình model muốn dùng cho request này (tùy chọn). "
            "Định dạng UUID, ví dụ: 8d2f0e5c-bf9a-4e14-b1fd-9e0b8a2a8d60"
        ),
        examples=["8d2f0e5c-bf9a-4e14-b1fd-9e0b8a2a8d60"],
    )
    confirmed_action_id: Optional[str] = Field(
        None,
        description=(
            "ID của pending action đã được user confirm. "
            "Khi gửi field này, AI sẽ skip routing và thực thi action đã confirm."
        ),
    )
    image: Optional[ImageAttachmentRequest] = Field(
        None,
        description=(
            "Ảnh đính kèm (tùy chọn). "
            "FE cần upload ảnh lên ImageKit trước rồi gửi URL vào đây. "
            "Tối đa 1 ảnh/message. Hỗ trợ: JPEG, PNG, WebP (≤ 5MB)"
        ),
    )
    context_scope: Optional[ChatContextScope] = Field(
        None,
        description="Override phạm vi tài liệu cho riêng lượt chat này",
    )
    assistant_config: Optional[ChatAssistantConfigPayload] = Field(
        default=None,
        description="Override capability cho riêng lượt chat này",
    )
    context_id: Optional[str] = Field(
        None,
        description="Override ID theo scope cho riêng lượt chat này",
    )
    context_options: Optional[dict[str, Any]] = Field(
        None,
        description="Override tùy chọn scope, bao gồm document_ids cho custom_docs",
    )
    inline_history: Optional[list[dict[str, str]]] = Field(
        None,
        max_length=20,
        description=(
            "Bounded recent chat history gửi từ FE (GENERAL mode only). "
            "Mỗi item: {role: 'user'|'assistant', content: str}. "
            "Tối đa 20 items. Khi có field này và mode=general, BE sẽ dùng ngay mà không fetch DB."
        ),
    )

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_send_mode(cls, value: object) -> object:
        if value is None:
            return None
        return normalize_chat_mode(value if isinstance(value, (str, ChatMode)) else None)

    @field_validator("toolset", mode="before")
    @classmethod
    def normalize_send_toolset(cls, value: object) -> object:
        if value is None:
            return None
        return normalize_chat_toolset(
            value if isinstance(value, (str, ChatToolset)) else None
        )

    @model_validator(mode="after")
    def validate_send_scope_fields(self) -> "SendMessageRequest":
        if self.assistant_config is not None:
            self.assistant_config = ChatAssistantConfigPayload(
                **normalize_assistant_config(
                    self.assistant_config.model_dump(),
                    self.mode,
                    self.toolset,
                )
            )
            if not self.assistant_config.internal_enabled:
                self.toolset = ChatToolset.NONE if self.toolset is not None else self.toolset

        if self.context_scope == "custom_docs":
            document_ids = (self.context_options or {}).get("document_ids") or []
            if not isinstance(document_ids, list) or not document_ids:
                raise ValueError("context_options.document_ids is required when context_scope=custom_docs")
            self.context_id = None
        elif self.context_scope in {"project", "folder", "document"} and not self.context_id:
            raise ValueError(f"context_id is required when context_scope={self.context_scope}")
        elif self.context_scope and self.context_scope not in {"project", "folder", "document"}:
            self.context_id = None
        return self


class CitationResponse(BaseModel):
    document_id: str
    chunk_index: int
    file_name: str
    relevance_score: float
    cited_content: str
    chunk_count: int = 1


class ImageAttachmentResponse(BaseModel):
    url: str
    mime_type: str
    original_name: Optional[str] = None


class MessageResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "m1e2s3g4-0000-1111-2222-abcdef012345",
                "conversation_id": "c1d2e3f4-1234-5678-abcd-ef0123456789",
                "role": "assistant",
                "content": "Theo chính sách HR, nhân viên được nghỉ 12 ngày phép/năm.",
                "mode": "rag",
                "image": None,
                "citations": [],
                "web_sources": [],
                "tokens_used": 312,
                "created_at": "2024-01-15T09:01:05Z",
            }
        }
    )

    id: str
    conversation_id: str
    role: str
    content: str
    # Ảnh đính kèm (chỉ có ở user message)
    image: Optional[ImageAttachmentResponse] = None
    mode: Optional[str] = None
    intent: Optional[str] = None
    citations: list[CitationResponse] = Field(default_factory=list)
    web_sources: list[dict] = Field(default_factory=list)
    model_name: Optional[str] = None
    model_used: Optional[str] = None
    model_config_id: Optional[str] = None
    tokens_used: Optional[int] = None
    documents_retrieved: Optional[int] = None
    response_time_ms: Optional[int] = None
    created_at: datetime


class ConversationHistoryResponse(BaseModel):
    conversation_id: str
    messages: list[MessageResponse]
    total: int
    limit: int
    offset: int


class PendingActionResponse(BaseModel):
    id: str
    conversation_id: str
    tool_name: str
    preview: str
    status: str
    error: Optional[str] = None
    created_at: datetime
    expires_at: datetime


class ChatStreamChunk(BaseModel):
    type: str = Field(
        ...,
        description=(
            "`token` | `tool_call` | `tool_result` | `confirm_required` | "
            "`done` | `suggestions` | `error`"
        ),
    )
    seq: Optional[int] = None
    content: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    action_id: Optional[str] = None
    preview: Optional[str] = None
    tool_name: Optional[str] = None
    questions: Optional[list[str]] = None
    citations: Optional[list[CitationResponse]] = None
    web_sources: Optional[list[dict]] = None
    message_id: Optional[str] = None
    response_time_ms: Optional[int] = None
    tokens_used: Optional[int] = None
    grounded: Optional[bool] = None
    abstained: Optional[bool] = None
    evidence_status: Optional[str] = None
    error: Optional[str] = None
    code: Optional[str] = None
    retryable: Optional[bool] = None
