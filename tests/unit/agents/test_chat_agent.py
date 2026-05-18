import pytest
from unittest.mock import AsyncMock, patch

from app.agents.chat_agent import ChatAgent, ChatInput, ChatOutput
from app.agents.retrieval_agent import RetrievedChunk
from app.services.llm_service import LLMResponse


def _make_chunk(doc_id="doc-1", score=0.9) -> RetrievedChunk:
    return RetrievedChunk(
        vector_id=f"{doc_id}_0",
        document_id=doc_id,
        chunk_index=0,
        content="Chính sách nghỉ phép: nhân viên được nghỉ 12 ngày/năm.",
        score=score,
        file_name="hr_policy.pdf",
        file_type="pdf",
        access_scope="organization",
    )


@pytest.mark.asyncio
async def test_chat_agent_rag_mode():
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(
            content="Nhân viên được nghỉ 12 ngày phép mỗi năm.",
            tokens_used=80,
            model="gpt-4o-mini",
        )
    )):
        agent = ChatAgent()
        result = await agent.run(
            ChatInput(
                organization_id="org-1",
                user_id="user-1",
                conversation_id="conv-1",
                user_message="Tôi được nghỉ bao nhiêu ngày?",
                retrieved_chunks=[_make_chunk()],
                history=[],
                mode="rag",
            )
        )

    assert isinstance(result, ChatOutput)
    assert result.success is True
    assert "12" in result.response_text
    assert result.tokens_used == 80
    assert len(result.citations) == 1


@pytest.mark.asyncio
async def test_chat_agent_direct_mode():
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(content="Xin chào! Tôi có thể giúp gì?", tokens_used=20, model="gpt-4o-mini")
    )):
        agent = ChatAgent()
        result = await agent.run(
            ChatInput(
                organization_id="org-1",
                user_id="user-1",
                conversation_id="conv-1",
                user_message="Xin chào!",
                retrieved_chunks=[],
                history=[],
                mode="direct",
            )
        )

    assert result.success is True
    assert result.citations == []


@pytest.mark.asyncio
async def test_chat_agent_citations_capped_content():
    chunks = [_make_chunk(doc_id=f"doc-{i}", score=0.9 - i * 0.05) for i in range(5)]
    with patch("app.services.llm_service.LLMService.ainvoke", AsyncMock(
        return_value=LLMResponse(content="Trả lời.", tokens_used=10, model="gpt-4o-mini")
    )):
        agent = ChatAgent()
        result = await agent.run(
            ChatInput(
                organization_id="org-1",
                user_id="user-1",
                conversation_id="conv-1",
                user_message="Câu hỏi test",
                retrieved_chunks=chunks,
                history=[],
                mode="rag",
            )
        )

    assert len(result.citations) == 5
    for citation in result.citations:
        assert len(citation.cited_content) <= 300


def test_chat_agent_build_messages_matches_user_language():
    agent = ChatAgent()
    messages = agent._build_messages(
        ChatInput(
            organization_id="org-1",
            user_id="user-1",
            conversation_id="conv-1",
            user_message="How many leave days do I have?",
            retrieved_chunks=[_make_chunk()],
            history=[],
            mode="rag",
        )
    )

    assert messages[0].role == "system"
    assert "tiếng Anh" in messages[0].content
