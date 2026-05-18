import pytest
from pydantic import ValidationError

from app.schemas.chat import CreateConversationRequest, SendMessageRequest


def test_default_conversation_is_general_assistant_without_tools():
    body = CreateConversationRequest()

    assert body.mode.value == "general"
    assert body.toolset.value == "none"
    assert body.assistant_config.internal_enabled is False
    assert body.assistant_config.external_enabled is False
    assert body.context_scope == "organization"


def test_custom_docs_scope_requires_document_ids_on_create():
    with pytest.raises(ValidationError):
        CreateConversationRequest(mode="rag", context_scope="custom_docs")

    body = CreateConversationRequest(
        mode="rag",
        context_scope="custom_docs",
        context_options={"document_ids": ["doc-1", "doc-2"]},
    )

    assert body.context_id is None
    assert body.context_options["document_ids"] == ["doc-1", "doc-2"]


def test_send_message_accepts_per_turn_custom_docs_scope():
    body = SendMessageRequest(
        content="Only answer from these documents",
        mode="rag",
        context_scope="custom_docs",
        context_options={"document_ids": ["doc-1", "doc-2"]},
    )

    assert body.context_scope == "custom_docs"
    assert body.context_options["document_ids"] == ["doc-1", "doc-2"]


def test_general_mode_ignores_stale_doc_scope_when_internal_tool_is_off():
    body = CreateConversationRequest(
        mode="general",
        toolset="none",
        assistant_config={
            "internal_enabled": False,
            "external_enabled": False,
            "external_mode": "web_search",
        },
        context_scope="custom_docs",
    )

    assert body.mode.value == "general"
    assert body.toolset.value == "none"
    assert body.assistant_config.internal_enabled is False


def test_combined_capabilities_resolve_to_auto_mode():
    body = CreateConversationRequest(
        mode="general",
        toolset="tasks",
        assistant_config={
            "internal_enabled": True,
            "external_enabled": True,
            "external_mode": "web_search",
        },
    )

    assert body.mode.value == "auto"
    assert body.toolset.value == "tasks"
    assert body.assistant_config.external_enabled is True
