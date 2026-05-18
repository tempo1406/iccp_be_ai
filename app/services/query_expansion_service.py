from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

import structlog

from app.core.config import settings
from app.schemas.ai_model_config import AIModelPurpose
from app.services.language_service import LanguageService
from app.services.llm_service import ChatMessage, LLMService

log = structlog.get_logger(__name__)

_QUERY_EXPANSION_CACHE_TTL = 3600
_MAX_SEARCH_QUERIES = 3
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis

        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _cache_key(query: str) -> str:
    digest = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()
    return f"queryexp:{digest}"


def _strip_json_fence(raw: str) -> str:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _normalize_language_code(raw: str, fallback: str) -> str:
    value = (raw or "").strip().lower()
    if value in {"vi", "vn", "vietnamese"}:
        return "vi"
    if value in {"en", "eng", "english"}:
        return "en"
    if value in {"mixed", "multi", "multilingual"}:
        return "mixed"
    return fallback


def _dedupe_queries(queries: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for query in queries:
        normalized = re.sub(r"\s+", " ", (query or "").strip())
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(normalized)
    return result[:_MAX_SEARCH_QUERIES]


@dataclass
class QueryExpansionResult:
    detected_language: str
    search_queries: list[str]


class QueryExpansionService:
    @classmethod
    async def expand(
        cls,
        query: str,
        *,
        organization_id: Optional[str] = None,
    ) -> QueryExpansionResult:
        normalized_query = re.sub(r"\s+", " ", (query or "").strip())
        detected_language = LanguageService.detect_language(normalized_query)
        fallback = QueryExpansionResult(
            detected_language=detected_language,
            search_queries=[normalized_query] if normalized_query else [],
        )

        if not normalized_query:
            return fallback

        if not settings.ENABLE_QUERY_EXPANSION or len(normalized_query) < 8:
            return fallback

        key = _cache_key(normalized_query)
        redis = None
        cached = None
        try:
            redis = _get_redis()
            cached = await redis.get(key)
        except Exception as exc:
            log.warning(
                "query_expansion.cache_read_failed",
                query=normalized_query[:80],
                error=str(exc),
            )
        if cached:
            try:
                payload = json.loads(cached)
                return QueryExpansionResult(
                    detected_language=_normalize_language_code(
                        str(payload.get("detected_language", "")),
                        detected_language,
                    ),
                    search_queries=_dedupe_queries(
                        [normalized_query] + list(payload.get("search_queries") or [])
                    )
                    or [normalized_query],
                )
            except Exception:
                log.warning("query_expansion.cache_parse_failed", query=normalized_query[:80])

        try:
            response = await LLMService.ainvoke(
                [
                    ChatMessage(
                        role="system",
                        content=(
                            "You are a multilingual retrieval query expander for an enterprise RAG system.\n"
                            "Return STRICT JSON only with this schema:\n"
                            '{"detected_language":"vi|en|mixed|unknown","alternate_queries":["..."]}\n'
                            "Rules:\n"
                            "- Preserve the user's intent exactly.\n"
                            "- Do not add facts, dates, names, or assumptions.\n"
                            "- Produce at most 2 alternate_queries.\n"
                            "- If the query is Vietnamese, include a natural English retrieval query.\n"
                            "- If the query is English, include a natural Vietnamese retrieval query.\n"
                            "- If the query is mixed, include one clear Vietnamese query and one clear English query.\n"
                            "- Keep each alternate query concise and search-friendly."
                        ),
                    ),
                    ChatMessage(role="human", content=normalized_query),
                ],
                organization_id=organization_id,
                purpose=AIModelPurpose.CHAT_COMPLETION,
                max_tokens=200,
                temperature=0.0,
            )
            parsed = json.loads(_strip_json_fence(response.content))
            result = QueryExpansionResult(
                detected_language=_normalize_language_code(
                    str(parsed.get("detected_language", "")),
                    detected_language,
                ),
                search_queries=_dedupe_queries(
                    [normalized_query] + list(parsed.get("alternate_queries") or [])
                )
                or [normalized_query],
            )
        except Exception as exc:
            log.warning(
                "query_expansion.failed",
                query=normalized_query[:80],
                error=str(exc),
            )
            return fallback

        if redis is not None:
            try:
                await redis.setex(
                    key,
                    _QUERY_EXPANSION_CACHE_TTL,
                    json.dumps(
                        {
                            "detected_language": result.detected_language,
                            "search_queries": result.search_queries,
                        }
                    ),
                )
            except Exception as exc:
                log.warning(
                    "query_expansion.cache_write_failed",
                    query=normalized_query[:80],
                    error=str(exc),
                )

        return result
