from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncGenerator, Callable, Optional

import httpx
import structlog
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

log = structlog.get_logger(__name__)

_RETRY_DELAYS = [3, 8]


@dataclass
class BeeknoeeResponse:
    """Structured response from Beeknoee API (OpenAI-compatible)."""

    content: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


def _lc_to_chat_messages(messages: list) -> list[dict]:
    result = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            result.append({"role": "system", "content": str(msg.content)})
        elif isinstance(msg, HumanMessage):
            result.append({"role": "user", "content": str(msg.content)})
        elif isinstance(msg, AIMessage):
            result.append({"role": "assistant", "content": str(msg.content)})
        else:
            result.append({"role": "user", "content": str(getattr(msg, "content", msg))})
    return result


def _extract_message_content(message: object) -> str:
    if isinstance(message, str):
        return message

    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if text:
                    parts.append(str(text))
        return "\n".join(part for part in parts if part).strip()

    if isinstance(message, dict):
        text = message.get("text") or message.get("content") or ""
        return str(text).strip()

    return str(message or "").strip()


def _extract_usage(data: dict) -> tuple[int, int, int]:
    """Extract token usage from OpenAI-compatible response."""
    usage = data.get("usage") if isinstance(data, dict) else None
    if usage is None and isinstance(data, dict) and any(
        key in data for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    ):
        # Accept both payload styles:
        # 1) {"usage": {...}}
        # 2) {...usage fields directly...}
        usage = data
    usage = usage or {}

    def _read_int(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                return int(value)
            if isinstance(value, str):
                try:
                    return int(float(value.strip()))
                except ValueError:
                    continue
        return 0

    prompt_tokens = _read_int("prompt_tokens", "input_tokens", "promptTokenCount")
    completion_tokens = _read_int("completion_tokens", "output_tokens", "candidatesTokenCount")
    total_tokens = _read_int("total_tokens", "totalTokenCount")

    if total_tokens == 0:
        total_details = usage.get("total_tokens_details") or usage.get("tokenUsage") or {}
        if isinstance(total_details, dict):
            total_tokens = int(total_details.get("total_tokens", 0) or 0)

    # Fallback: compute total if only prompt + completion are present
    if total_tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens
    return prompt_tokens, completion_tokens, total_tokens


async def beeknoee_astream(
    messages: list,
    api_key: str,
    model: str,
    base_url: str,
    max_tokens: int = 65536,
    temperature: float = 0.7,
    extra_headers: dict | None = None,
    on_usage: Optional[Callable[[int, int, int], None]] = None,
) -> AsyncGenerator[str, None]:
    payload = {
        "model": model,
        "messages": _lc_to_chat_messages(messages),
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    url = f"{base_url.rstrip('/')}/chat/completions"

    last_error: Exception | None = None
    attempts = [None] + _RETRY_DELAYS

    for attempt_idx, delay in enumerate(attempts):
        if delay is not None:
            log.warning("beeknoee.retry", attempt=attempt_idx, delay=delay)
            await asyncio.sleep(delay)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    if resp.status_code == 429:
                        body = await resp.aread()
                        last_error = RuntimeError(
                            f"Rate-limited (429): {body.decode(errors='replace')[:300]}"
                        )
                        log.warning("beeknoee.rate_limited", attempt=attempt_idx)
                        continue

                    if resp.status_code != 200:
                        body = await resp.aread()
                        raise RuntimeError(
                            f"API error {resp.status_code}: {body.decode(errors='replace')[:500]}"
                        )

                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line or not line.startswith("data:"):
                            continue

                        data = line[5:].strip()
                        if data == "[DONE]":
                            return

                        try:
                            chunk = json.loads(data)
                            usage_obj = chunk.get("usage")
                            if isinstance(usage_obj, dict):
                                p, c, t = _extract_usage({"usage": usage_obj})
                                if on_usage:
                                    on_usage(p, c, t)

                            delta = chunk["choices"][0]["delta"]
                            content = delta.get("content") or ""
                            if content:
                                yield content
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
                    return
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Request failed: {exc}") from exc

    raise last_error or RuntimeError("All retries exhausted")


async def beeknoee_ainvoke(
    messages: list,
    api_key: str,
    model: str,
    base_url: str,
    max_tokens: int = 2048,
    temperature: float = 0.2,
    extra_headers: dict | None = None,
) -> BeeknoeeResponse:
    payload = {
        "model": model,
        "messages": _lc_to_chat_messages(messages),
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **(extra_headers or {}),
    }
    url = f"{base_url.rstrip('/')}/chat/completions"

    last_error: Exception | None = None
    attempts = [None] + _RETRY_DELAYS

    for attempt_idx, delay in enumerate(attempts):
        if delay is not None:
            log.warning("beeknoee.retry", attempt=attempt_idx, delay=delay)
            await asyncio.sleep(delay)

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code == 429:
                    last_error = RuntimeError(f"Rate-limited (429): {resp.text[:300]}")
                    log.warning("beeknoee.rate_limited", attempt=attempt_idx)
                    continue

                if resp.status_code != 200:
                    raise RuntimeError(f"API error {resp.status_code}: {resp.text[:500]}")

                data = resp.json()
                choices = data.get("choices") or []
                if not choices:
                    return BeeknoeeResponse(content="")

                message = choices[0].get("message") or {}
                content = _extract_message_content(message.get("content"))
                prompt_tokens, completion_tokens, total_tokens = _extract_usage(data)

                log.info(
                    "beeknoee.tokens",
                    model=model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )

                return BeeknoeeResponse(
                    content=content,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Request failed: {exc}") from exc

    raise last_error or RuntimeError("All retries exhausted")
