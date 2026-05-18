from __future__ import annotations

import asyncio
import json
import re
from typing import AsyncGenerator, Optional

import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.core.config import settings
from app.prompts.landing_page import (
    LANDING_PAGE_SYSTEM_PROMPT,
    build_conversation_context,
    resolve_generation_mode,
)
from app.schemas.ai_model_config import AIModelPurpose, AIModelProvider
from app.schemas.landing_page import LandingPageConversationMessage, OrgContext
from app.services.ai_model_config_service import AIModelConfigService
from app.services.beeknoee_service import beeknoee_ainvoke, beeknoee_astream
from app.services.llm_service import LLMService
from app.services.quota_service import QuotaService

log = structlog.get_logger(__name__)


def _extract_first_html_document(raw: str) -> str:
    text = raw.strip().lstrip("\ufeff")
    fenced = re.search(r"```(?:html)?\s*\n?([\s\S]*?)\n?```", text, re.IGNORECASE)
    candidates = []
    if fenced and fenced.group(1).strip():
        candidates.append(fenced.group(1).strip())
    if text:
        candidates.append(text)

    for candidate in candidates:
        lower = candidate.lower()
        starts = [idx for idx in (lower.find("<!doctype"), lower.find("<html")) if idx >= 0]
        start_idx = min(starts) if starts else -1
        sliced = candidate[start_idx:].strip() if start_idx >= 0 else candidate.strip()
        lower_sliced = sliced.lower()
        end_idx = lower_sliced.find("</html>")

        if end_idx >= 0:
            return sliced[: end_idx + len("</html>")].strip()
        if start_idx >= 0:
            return sliced

    return text


def _sanitize_html_output(raw: str) -> str:
    """Clean up common model output mistakes before the frontend applies the result."""
    text = _extract_first_html_document(raw)

    if not text:
        return ""

    if not text.lower().startswith("<!doctype"):
        html_pos = text.lower().find("<html")
        if html_pos >= 0:
            text = "<!DOCTYPE html>\n" + text[html_pos:]

    text = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+on[a-z-]+\s*=\s*(?:\"[^\"]*\"|'[^']*')", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```[\s\S]*?(?=(?:</body>|</html>|$))", "", text, flags=re.IGNORECASE)

    end_idx = text.lower().find("</html>")
    if end_idx >= 0:
        text = text[: end_idx + len("</html>")]

    return text.strip()


def _strip_html_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def _summarize_current_html(current_html: Optional[str]) -> str:
    if not current_html or not current_html.strip():
        return "No current canvas HTML."

    headings = [
        _strip_html_tags(match)
        for match in re.findall(r"<h[1-3][^>]*>([\s\S]*?)</h[1-3]>", current_html, flags=re.IGNORECASE)
    ]
    headings = [heading for heading in headings if heading][:5]
    sections = re.findall(
        r'<(?:section|div)[^>]+(?:id|class)="([^"]+)"',
        current_html,
        flags=re.IGNORECASE,
    )
    sections = [section.strip() for section in sections if section.strip()][:8]

    summary_parts: list[str] = []
    if headings:
        summary_parts.append("Headings: " + " | ".join(headings))
    if sections:
        summary_parts.append("Section markers: " + " | ".join(sections))

    if not summary_parts:
        summary_parts.append(current_html[:1200])

    return "\n".join(summary_parts)


def _parse_visible_status_lines(raw: str) -> list[str]:
    text = raw.strip()
    if not text:
        return []

    match = re.search(r"\[[\s\S]*\]", text)
    candidate = match.group(0) if match else text

    try:
        data = json.loads(candidate)
        if isinstance(data, list):
            return [
                str(item).strip()
                for item in data
                if isinstance(item, str) and str(item).strip()
            ][:5]
    except Exception:
        pass

    fallback_lines = [
        line.strip("-* \n\r\t")
        for line in re.split(r"[\r\n]+", text)
        if line.strip()
    ]
    return fallback_lines[:5]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


async def _generate_visible_status_lines(
    org_context: OrgContext,
    user_prompt: str,
    mode: str,
    current_html: Optional[str],
    *,
    organization_id: str,
    custom_api_key: Optional[str],
) -> list[str]:
    summary = _summarize_current_html(current_html)
    prompt = (
        "Generate 4 short user-visible working notes for a landing page editor.\n"
        "These are NOT private chain-of-thought. They are concise progress notes the UI can stream live.\n"
        "Return JSON array only. No markdown. No explanation.\n"
        "Each note must be concrete, under 14 words, and match the user's language.\n\n"
        f"Mode: {mode}\n"
        f"Organization: {org_context.name}\n"
        f"Industry: {org_context.industry or 'Not specified'}\n"
        f"User request: {user_prompt.strip()}\n"
        f"Current canvas summary:\n{summary}"
    )
    status_messages = [
        SystemMessage(
            content=(
                "You create short visible progress notes for UI streaming. "
                "Never reveal hidden reasoning. Output JSON array only."
            )
        ),
        HumanMessage(content=prompt),
    ]

    try:
        runtime = await AIModelConfigService.resolve_model_config(
            purpose=AIModelPurpose.LANDING_PAGE_STATUS,
            organization_id=organization_id,
            custom_api_key=custom_api_key,
        )
        if runtime.provider == AIModelProvider.BEEKNOEE:
            content = await beeknoee_ainvoke(
                status_messages,
                api_key=runtime.api_key,
                model=runtime.model_name,
                base_url=runtime.api_base_url or settings.beeknoee_api_base,
                max_tokens=512,
                temperature=0.2,
            )
            return _parse_visible_status_lines(content)

        response = await LLMService.ainvoke_lc_messages(
            status_messages,
            organization_id=organization_id,
            purpose=AIModelPurpose.LANDING_PAGE_STATUS,
            custom_api_key=custom_api_key,
            max_tokens=512,
            temperature=0.2,
        )
        return _parse_visible_status_lines(response.content)
    except Exception as exc:
        log.warning("landing_page_agent.status_generation_failed", error=str(exc))
        return []


class LandingPageAgent:
    """Agent that generates or modifies landing pages via streaming HTML output.

    Resolves the provider from Mongo-backed model config first, then falls back
    to Beeknoee or Gemini env settings for backward compatibility.
    """

    async def stream(
        self,
        org_context: OrgContext,
        conversation: list[LandingPageConversationMessage],
        mode: Optional[str],
        user_prompt: str,
        current_html: Optional[str],
        custom_api_key: Optional[str],
        *,
        user_id: str,
        organization_id: str,
    ) -> AsyncGenerator[bytes, None]:
        effective_mode = resolve_generation_mode(mode, current_html)
        status_lines = await _generate_visible_status_lines(
            org_context=org_context,
            user_prompt=user_prompt,
            mode=effective_mode,
            current_html=current_html,
            organization_id=organization_id,
            custom_api_key=custom_api_key,
        )
        lc_messages = [
            SystemMessage(content=LANDING_PAGE_SYSTEM_PROMPT),
            HumanMessage(
                content=build_conversation_context(
                    org_context=org_context,
                    mode=effective_mode,
                    current_html=current_html,
                )
            ),
        ]

        for turn in conversation[-10:]:
            content = turn.content.strip()
            if not content:
                continue
            if turn.role == "user":
                lc_messages.append(HumanMessage(content=f"Previous user request:\n{content}"))
            else:
                lc_messages.append(
                    AIMessage(
                        content=(
                            "I previously responded in the editor conversation and updated the landing page "
                            f"based on this request summary:\n{content}"
                        )
                    )
                )

        lc_messages.append(HumanMessage(content=f"Latest user request:\n{user_prompt.strip()}"))
        # Prefill the assistant response to guarantee output starts with valid HTML.
        # The provider will continue generating from this point without preamble.
        lc_messages.append(AIMessage(content="<!DOCTYPE html>"))

        runtime = await AIModelConfigService.resolve_model_config(
            purpose=AIModelPurpose.LANDING_PAGE_GENERATION,
            organization_id=organization_id,
            custom_api_key=custom_api_key,
        )

        log.info(
            "landing_page_agent.stream",
            provider=runtime.provider.value,
            model=runtime.model_name,
            mode=effective_mode,
        )

        try:
            if runtime.provider == AIModelProvider.BEEKNOEE:
                token_gen = beeknoee_astream(
                    lc_messages,
                    api_key=runtime.api_key,
                    model=runtime.model_name,
                    base_url=runtime.api_base_url or settings.beeknoee_api_base,
                    max_tokens=65536,
                    temperature=0.7,
                )
            else:
                token_gen = LLMService.astream_lc_messages(
                    lc_messages,
                    organization_id=organization_id,
                    purpose=AIModelPurpose.LANDING_PAGE_GENERATION,
                    custom_api_key=custom_api_key,
                    max_tokens=65536,
                    temperature=0.7,
                )

            # Prepend the prefilled assistant content so accumulated is a complete HTML doc.
            prefill = "<!DOCTYPE html>"
            accumulated = prefill
            status_index = 0
            status_thresholds = [1200, 5000, 14000, 26000]

            if status_lines:
                yield (
                    "data: "
                    + json.dumps({"type": "status", "message": status_lines[0]})
                    + "\n\n"
                ).encode()
                status_index = 1

            yield (
                "data: " + json.dumps({"type": "chunk", "content": prefill}) + "\n\n"
            ).encode()

            async for token in token_gen:
                accumulated += token
                yield (
                    "data: " + json.dumps({"type": "chunk", "content": token}) + "\n\n"
                ).encode()

                while status_index < len(status_lines):
                    threshold_idx = min(status_index - 1, len(status_thresholds) - 1)
                    if threshold_idx < 0 or len(accumulated) < status_thresholds[threshold_idx]:
                        break
                    yield (
                        "data: "
                        + json.dumps({"type": "status", "message": status_lines[status_index]})
                        + "\n\n"
                    ).encode()
                    status_index += 1

            sanitized = _sanitize_html_output(accumulated)
            if sanitized and sanitized != accumulated:
                log.info(
                    "landing_page_agent.sanitized_output",
                    original_len=len(accumulated),
                    sanitized_len=len(sanitized),
                )
                yield (
                    "data: " + json.dumps({"type": "replace", "content": sanitized}) + "\n\n"
                ).encode()

            while status_index < len(status_lines):
                yield (
                    "data: "
                    + json.dumps({"type": "status", "message": status_lines[status_index]})
                    + "\n\n"
                ).encode()
                status_index += 1

            final_html = sanitized or accumulated
            tokens_used = _estimate_tokens(final_html)
            token_task = asyncio.create_task(
                QuotaService.increment_tokens(user_id, organization_id, tokens_used)
            )
            token_task.add_done_callback(
                lambda task: log.error(
                    "landing_page_agent.token_quota_update_failed",
                    error=str(task.exception()),
                    user_id=user_id,
                    organization_id=organization_id,
                )
                if not task.cancelled() and task.exception()
                else None
            )

            yield (
                b"data: "
                + json.dumps({"type": "done", "tokens_used": tokens_used}).encode()
                + b"\n\n"
            )

        except Exception as exc:
            log.exception("landing_page_agent.error", error=str(exc))
            yield (
                "data: " + json.dumps({"type": "error", "message": str(exc)}) + "\n\n"
            ).encode()
