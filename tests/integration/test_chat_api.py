import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_send_message_streams_sse(test_client, valid_jwt_token):
    async def mock_stream(orchestrator_input):
        yield {"type": "token", "content": "Xin"}
        yield {"type": "token", "content": " chào"}
        yield {"type": "done", "message_id": "msg-1", "citations": [], "response_time_ms": 100}

    with patch("app.api.v1.chat._orchestrator.stream", side_effect=mock_stream):
        response = await test_client.post(
            "/api/v1/chat/conversations/conv-test-id/messages",
            json={"content": "Xin chào!"},
            headers={"Authorization": f"Bearer {valid_jwt_token}"},
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    body = response.text
    assert "token" in body
    assert "done" in body


@pytest.mark.asyncio
async def test_send_message_requires_auth(test_client):
    response = await test_client.post(
        "/api/v1/chat/conversations/conv-1/messages",
        json={"content": "Test message"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_health_endpoint(test_client):
    response = await test_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["service"] == "iccp_be_ai"
