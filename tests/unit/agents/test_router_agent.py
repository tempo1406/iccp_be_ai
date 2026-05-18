import pytest
from unittest.mock import AsyncMock, patch

from app.agents.router_agent import RouterAgent, RouterInput, RouterOutput
from app.services.llm_service import LLMResponse


@pytest.mark.asyncio
async def test_router_document_query():
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(content="DOCUMENT_QUERY", tokens_used=5, model="gpt-4o-mini")
    )):
        agent = RouterAgent()
        result = await agent.run(
            RouterInput(
                organization_id="org-1",
                user_id="user-1",
                message="Chính sách nghỉ phép của công ty là gì?",
            )
        )
    assert result.intent == "DOCUMENT_QUERY"


@pytest.mark.asyncio
async def test_router_chitchat():
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(content="CHITCHAT", tokens_used=3, model="gpt-4o-mini")
    )):
        agent = RouterAgent()
        result = await agent.run(
            RouterInput(
                organization_id="org-1",
                user_id="user-1",
                message="Xin chào!",
            )
        )
    assert result.intent == "CHITCHAT"


@pytest.mark.asyncio
async def test_router_defaults_on_unknown_intent():
    """Unknown LLM response should default to DOCUMENT_QUERY."""
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(content="UNKNOWN_THING", tokens_used=3, model="gpt-4o-mini")
    )):
        agent = RouterAgent()
        result = await agent.run(
            RouterInput(
                organization_id="org-1",
                user_id="user-1",
                message="something unclear",
            )
        )
    assert result.intent == "DOCUMENT_QUERY"


@pytest.mark.asyncio
async def test_router_task_without_context_downgrades():
    """TASK_QUERY without project context_id should route to TOOL_QUERY."""
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(content="TASK_QUERY", tokens_used=3, model="gpt-4o-mini")
    )):
        agent = RouterAgent()
        result = await agent.run(
            RouterInput(
                organization_id="org-1",
                user_id="user-1",
                message="task deadline?",
                context_id=None,
            )
        )
    assert result.intent == "TOOL_QUERY"
