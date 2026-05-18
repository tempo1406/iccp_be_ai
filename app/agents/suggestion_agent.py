from __future__ import annotations

import json
import re

import structlog

from app.schemas.ai_model_config import AIModelPurpose
from app.services.llm_service import ChatMessage, LLMService

log = structlog.get_logger(__name__)

_TIMEOUT_S = 8  # abort suggestions if LLM takes too long

_SYSTEM_PROMPT = """\
You are a follow-up question generator for a chatbot assistant.
Given a user's question and the assistant's answer, generate {count} concise follow-up questions that the user might naturally want to ask next.

Rules:
- Questions must be DIRECTLY related to the current conversation topic
- Each question should explore a DIFFERENT angle (deeper detail, comparison, practical usage, etc.)
- Keep each question SHORT (under 15 words)
- Match the language of the user's message (Vietnamese → Vietnamese, English → English)
- For RAG/internal mode: focus on company policies, procedures, documentation
- For auto/web mode: focus on broader knowledge, recent facts, comparisons
- Output ONLY a JSON array of strings, nothing else

Example output:
["Câu hỏi 1?", "Câu hỏi 2?", "Câu hỏi 3?"]
"""

_USER_TEMPLATE = """\
Mode: {mode}
User question: {user_message}
Assistant answer (summary): {response_summary}

Generate {count} follow-up questions as a JSON array."""


class SuggestionAgent:
    """
    Lightweight LLM call that generates follow-up question suggestions.
    Called after the main response stream completes.
    """

    @staticmethod
    async def generate(
        user_message: str,
        assistant_response: str,
        mode: str = "rag",
        count: int | None = None,
        organization_id: str | None = None,
    ) -> list[str]:
        """
        Generate follow-up suggestions.
        Returns empty list on any error (never raises).
        """
        # More suggestions for open-ended web/hybrid modes
        if count is None:
            count = 3 if mode == "rag" else 4

        # Truncate response to keep prompt small
        response_summary = assistant_response[:600]

        try:
            import asyncio
            messages = [
                ChatMessage(
                    role="system",
                    content=_SYSTEM_PROMPT.format(count=count),
                ),
                ChatMessage(
                    role="human",
                    content=_USER_TEMPLATE.format(
                        mode=mode,
                        user_message=user_message[:300],
                        response_summary=response_summary,
                        count=count,
                    ),
                ),
            ]
            response = await asyncio.wait_for(
                LLMService.ainvoke(
                    messages,
                    organization_id=organization_id,
                    purpose=AIModelPurpose.SUGGESTION,
                ),
                timeout=_TIMEOUT_S,
            )
            questions = _parse_json_array(response.content)
            # Validate: non-empty strings, max 150 chars each, cap at count
            questions = [
                q.strip() for q in questions
                if isinstance(q, str) and 3 < len(q.strip()) < 150
            ][:count]

            log.info(
                "suggestion_agent.generated",
                mode=mode,
                count=len(questions),
            )
            return questions

        except Exception as exc:
            log.warning("suggestion_agent.failed", error=str(exc))
            return []


def _parse_json_array(raw: str) -> list:
    """Extract a JSON array from LLM output that may have surrounding text."""
    raw = raw.strip()
    # Try direct parse first
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        pass

    # Try to find [...] inside the text
    match = re.search(r'\[.*?\]', raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group())
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass

    # Last resort: split by newlines/bullets
    lines = [
        re.sub(r'^[\-\d\.\*\s"\']+|["\']+$', '', line).strip()
        for line in raw.splitlines()
        if line.strip() and not line.strip().startswith(('[', ']'))
    ]
    return [l for l in lines if l]
