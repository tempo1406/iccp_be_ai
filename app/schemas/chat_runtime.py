from __future__ import annotations

from enum import Enum
from typing import Any, Literal


class ChatMode(str, Enum):
    GENERAL = "general"
    AUTO = "auto"
    RAG = "rag"
    WEB = "web"


class ChatToolset(str, Enum):
    AUTO = "auto"
    NONE = "none"
    PROJECTS = "projects"
    TASKS = "tasks"
    TICKETS = "tickets"
    DOCUMENTS = "documents"
    ORGANIZATION = "organization"
    DAILY_REPORTS = "daily_reports"


ChatSearchMode = Literal["rag_internal", "hybrid", "web_only"]
ChatExternalMode = Literal["web_search"]
ChatContextScope = Literal[
    "organization",
    "project",
    "my_docs",
    "folder",
    "document",
    "custom_docs",
]

_VALID_CHAT_MODES = {mode.value for mode in ChatMode}
_VALID_CHAT_TOOLSETS = {toolset.value for toolset in ChatToolset}


def normalize_chat_mode(value: ChatMode | str | None, default: str = ChatMode.GENERAL.value) -> str:
    if isinstance(value, ChatMode):
        return value.value

    raw = (value or "").strip().lower()
    if raw in {"", "default", "general_assistant"}:
        return default
    if raw == "hybrid":
        return ChatMode.AUTO.value
    if raw in _VALID_CHAT_MODES:
        return raw
    return default


def normalize_chat_toolset(
    value: ChatToolset | str | None,
    default: str = ChatToolset.NONE.value,
) -> str:
    if isinstance(value, ChatToolset):
        return value.value

    raw = (value or "").strip().lower()
    if raw in _VALID_CHAT_TOOLSETS:
        return raw
    return default


def normalize_external_mode(
    value: ChatExternalMode | str | None,
    default: ChatExternalMode = "web_search",
) -> ChatExternalMode:
    return "web_search" if value == "web_search" else default


def derive_assistant_config_from_legacy(
    mode: ChatMode | str | None,
    toolset: ChatToolset | str | None,
) -> dict[str, Any]:
    normalized_mode = normalize_chat_mode(mode)
    normalized_toolset = normalize_chat_toolset(toolset)
    return {
        "internal_enabled": (
            normalized_toolset != ChatToolset.NONE.value
            or normalized_mode in {ChatMode.RAG.value, ChatMode.AUTO.value}
        ),
        "external_enabled": normalized_mode in {ChatMode.WEB.value, ChatMode.AUTO.value},
        "external_mode": "web_search",
    }


def normalize_assistant_config(
    value: dict[str, Any] | None,
    mode: ChatMode | str | None,
    toolset: ChatToolset | str | None,
) -> dict[str, Any]:
    fallback = derive_assistant_config_from_legacy(mode, toolset)
    if not isinstance(value, dict):
        return fallback

    return {
        "internal_enabled": (
            value.get("internal_enabled")
            if isinstance(value.get("internal_enabled"), bool)
            else fallback["internal_enabled"]
        ),
        "external_enabled": (
            value.get("external_enabled")
            if isinstance(value.get("external_enabled"), bool)
            else fallback["external_enabled"]
        ),
        "external_mode": normalize_external_mode(value.get("external_mode")),
    }


def resolve_chat_mode(
    mode: ChatMode | str | None,
    toolset: ChatToolset | str | None,
    assistant_config: dict[str, Any] | None,
) -> str:
    normalized_toolset = normalize_chat_toolset(toolset)
    normalized_config = normalize_assistant_config(assistant_config, mode, toolset)
    internal_enabled = bool(normalized_config["internal_enabled"])
    external_enabled = bool(normalized_config["external_enabled"])

    if not internal_enabled and not external_enabled:
        return ChatMode.GENERAL.value
    if not internal_enabled and external_enabled:
        return ChatMode.WEB.value
    if internal_enabled and not external_enabled:
        return (
            ChatMode.GENERAL.value
            if normalized_toolset != ChatToolset.NONE.value
            else ChatMode.RAG.value
        )
    return ChatMode.AUTO.value


def search_mode_from_mode(value: ChatMode | str | None) -> ChatSearchMode:
    mode = normalize_chat_mode(value)
    if mode == ChatMode.RAG.value:
        return "rag_internal"
    if mode == ChatMode.WEB.value:
        return "web_only"
    return "hybrid"


def uses_internal_docs(value: ChatMode | str | None) -> bool:
    return normalize_chat_mode(value) in {ChatMode.AUTO.value, ChatMode.RAG.value}


def uses_web_search(value: ChatMode | str | None) -> bool:
    return normalize_chat_mode(value) in {ChatMode.AUTO.value, ChatMode.WEB.value}
