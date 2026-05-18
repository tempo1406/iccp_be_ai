"""
Unit tests for GENERAL mode short-circuit path.

Verifies that:
1. GENERAL mode never calls RouterAgent.run()
2. GENERAL mode never calls RetrievalAgent.run()
3. GENERAL mode with inline_history skips persistence.load_history()
4. RAG mode still calls retrieval (no short-circuit)
5. Internal tool mode still routes before entering tool flow
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.orchestrator.orchestrator import AgentOrchestrator, OrchestratorInput
from app.agents.tool_planning_agent import ToolPlanningOutput


def _make_input(
    mode: str = "general",
    toolset: str = "none",
    inline_history: list | None = None,
) -> OrchestratorInput:
    return OrchestratorInput(
        organization_id="org-1",
        user_id="user-1",
        conversation_id="conv-1",
        user_message="Xin chào, hôm nay bạn có thể giúp gì cho mình?",
        mode=mode,
        toolset=toolset,
        inline_history=inline_history,
        trace_id="trace-test",
    )


async def _drain(gen):
    events = []
    async for event in gen:
        events.append(event)
    return events


@pytest.mark.asyncio
async def test_general_mode_skips_router_and_retrieval():
    """GENERAL mode must not invoke RouterAgent or RetrievalAgent."""
    orch = AgentOrchestrator()

    router_mock = AsyncMock()
    retrieval_mock = AsyncMock()
    history_mock = AsyncMock(return_value=[])
    suggestion_mock = AsyncMock(return_value=[])

    async def fake_chat_stream(*_, **__):
        yield "Hello"

    with (
        patch.object(orch._router, "run", router_mock),
        patch.object(orch._retrieval, "run", retrieval_mock),
        patch.object(orch._persistence, "load_history", history_mock),
        patch.object(orch._persistence, "save_message", AsyncMock(return_value="msg-1")),
        patch("app.agents.orchestrator.orchestrator.QuotaService.check_and_increment", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.ContentPolicyService.check_user_input", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.QuotaService.increment_tokens", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.SuggestionAgent.generate", suggestion_mock),
        patch("app.agents.orchestrator.orchestrator.AIModelConfigService.resolve_model_config", AsyncMock(
            return_value=MagicMock(model_display_name="test-model", model_name="test-model")
        )),
        patch.object(orch._chat, "stream_response", fake_chat_stream),
    ):
        events = await _drain(orch.stream(_make_input(mode="general")))

    router_mock.assert_not_called()
    retrieval_mock.assert_not_called()

    types = [e["type"] for e in events]
    assert "token" in types
    assert "done" in types


@pytest.mark.asyncio
async def test_general_mode_with_inline_history_skips_db_fetch():
    """GENERAL mode + inline_history must NOT call persistence.load_history."""
    orch = AgentOrchestrator()

    history_mock = AsyncMock(return_value=[])
    inline = [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello!"}]

    async def fake_chat_stream(*_, **__):
        yield "OK"

    with (
        patch.object(orch._persistence, "load_history", history_mock),
        patch.object(orch._persistence, "save_message", AsyncMock(return_value="msg-1")),
        patch("app.agents.orchestrator.orchestrator.QuotaService.check_and_increment", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.ContentPolicyService.check_user_input", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.QuotaService.increment_tokens", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.SuggestionAgent.generate", AsyncMock(return_value=[])),
        patch("app.agents.orchestrator.orchestrator.AIModelConfigService.resolve_model_config", AsyncMock(
            return_value=MagicMock(model_display_name="test-model", model_name="test-model")
        )),
        patch.object(orch._chat, "stream_response", fake_chat_stream),
    ):
        await _drain(orch.stream(_make_input(mode="general", inline_history=inline)))

    history_mock.assert_not_called()


@pytest.mark.asyncio
async def test_rag_mode_calls_retrieval():
    """RAG mode must invoke RetrievalAgent (no general short-circuit)."""
    orch = AgentOrchestrator()

    from app.agents.retrieval_agent import RetrievalOutput

    retrieval_mock = AsyncMock(return_value=RetrievalOutput(success=True, chunks=[], query_embedding=[]))
    router_mock = AsyncMock(return_value=MagicMock(
        intent="DOCUMENT_QUERY",
        context_scope="organization",
        context_id=None,
    ))

    async def fake_chat_stream(*_, **__):
        yield "Answer"

    with (
        patch.object(orch._router, "run", router_mock),
        patch.object(orch._retrieval, "run", retrieval_mock),
        patch.object(orch._persistence, "load_history", AsyncMock(return_value=[])),
        patch.object(orch._persistence, "save_message", AsyncMock(return_value="msg-1")),
        patch("app.agents.orchestrator.orchestrator.QuotaService.check_and_increment", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.ContentPolicyService.check_user_input", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.QuotaService.increment_tokens", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.SuggestionAgent.generate", AsyncMock(return_value=[])),
        patch("app.agents.orchestrator.orchestrator.AIModelConfigService.resolve_model_config", AsyncMock(
            return_value=MagicMock(model_display_name="test-model", model_name="test-model")
        )),
        patch.object(orch._chat, "stream_response", fake_chat_stream),
    ):
        await _drain(orch.stream(_make_input(mode="rag")))

    retrieval_mock.assert_called_once()


@pytest.mark.asyncio
async def test_internal_toolset_routes_before_tool_flow():
    """Internal tool mode must not use the plain GENERAL short-circuit."""
    orch = AgentOrchestrator()

    router_mock = AsyncMock(return_value=MagicMock(
        intent="TOOL_QUERY",
        context_scope="organization",
        context_id=None,
    ))

    planning_mock = AsyncMock(return_value=ToolPlanningOutput(
        success=False, decision=None, error="No tool needed"
    ))

    async def fake_chat_stream(*_, **__):
        yield "OK"

    with (
        patch.object(orch._router, "run", router_mock),
        patch.object(orch._persistence, "load_history", AsyncMock(return_value=[])),
        patch.object(orch._persistence, "save_message", AsyncMock(return_value="msg-1")),
        patch.object(orch._tool_planning, "run", planning_mock),
        patch("app.agents.orchestrator.orchestrator.QuotaService.check_and_increment", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.ContentPolicyService.check_user_input", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.QuotaService.increment_tokens", AsyncMock()),
        patch("app.agents.orchestrator.orchestrator.SuggestionAgent.generate", AsyncMock(return_value=[])),
        patch("app.agents.orchestrator.orchestrator.AIModelConfigService.resolve_model_config", AsyncMock(
            return_value=MagicMock(model_display_name="test-model", model_name="test-model")
        )),
        patch.object(orch._chat, "stream_response", fake_chat_stream),
    ):
        await _drain(orch.stream(_make_input(mode="general", toolset="tasks")))

    router_mock.assert_called_once()
    planning_mock.assert_called_once()


def test_combined_capabilities_resolve_to_auto_mode():
    input = OrchestratorInput(
        organization_id="org-1",
        user_id="user-1",
        conversation_id="conv-1",
        user_message="Check my tasks and search the latest policy update",
        mode="general",
        toolset="tasks",
        assistant_config={
            "internal_enabled": True,
            "external_enabled": True,
            "external_mode": "web_search",
        },
        trace_id="trace-test",
    )

    assert input.mode == "auto"
    assert input.toolset == "tasks"
    assert input.internal_enabled is True
    assert input.external_enabled is True
