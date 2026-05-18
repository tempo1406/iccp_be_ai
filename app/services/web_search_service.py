from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# How long to sleep between retries when rate-limited (seconds)
_RETRY_DELAYS = [2, 5, 10]


def _ddgs_text(query: str, max_results: int) -> list[dict[str, Any]]:
    """
    Try DuckDuckGo text search with two strategies:
      1. API-based (fast, but rate-limited by IP)
      2. HTML scraping fallback (slower, but bypasses rate limit)

    Retries up to len(_RETRY_DELAYS) times on RatelimitException.
    """
    from duckduckgo_search import DDGS
    from duckduckgo_search.exceptions import RatelimitException

    def _parse(r: dict) -> dict:
        return {
            "title": r.get("title", ""),
            "url": r.get("href", "") or r.get("url", ""),
            "snippet": r.get("body", "") or r.get("snippet", ""),
        }

    last_exc: Exception | None = None

    for attempt, delay in enumerate([0] + _RETRY_DELAYS):
        if delay:
            log.warning("web_search.ratelimit_retry", attempt=attempt, sleep_s=delay, query=query[:60])
            time.sleep(delay)
        try:
            with DDGS() as ddgs:
                # Primary: API method
                results = [_parse(r) for r in ddgs.text(query, max_results=max_results)]
            if results:
                return results
        except RatelimitException as exc:
            last_exc = exc
            log.warning("web_search.ratelimited", attempt=attempt, query=query[:60])
            continue
        except Exception as exc:
            log.error("web_search.api_error", error=str(exc), query=query[:60])
            last_exc = exc
            break

    # Fallback: HTML scraping (different code-path, different rate-limit bucket)
    log.info("web_search.html_fallback", query=query[:60])
    try:
        with DDGS() as ddgs:
            results = [_parse(r) for r in ddgs.text(
                query,
                max_results=max_results,
                backend="html",
            )]
        if results:
            return results
    except Exception as exc:
        log.error("web_search.html_fallback_failed", error=str(exc), query=query[:60])
        last_exc = exc

    # Last resort: lite backend
    log.info("web_search.lite_fallback", query=query[:60])
    try:
        with DDGS() as ddgs:
            results = [_parse(r) for r in ddgs.text(
                query,
                max_results=max_results,
                backend="lite",
            )]
        if results:
            return results
    except Exception as exc:
        log.error("web_search.lite_fallback_failed", error=str(exc), query=query[:60])
        last_exc = exc

    log.error("web_search.all_methods_failed", query=query[:60], last_error=str(last_exc))
    return []


class WebSearchService:
    """Async web search using DuckDuckGo (sync library wrapped in executor)."""

    @staticmethod
    async def search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """
        Search DuckDuckGo for the given query.
        Returns list of {title, url, snippet} dicts.
        Automatically retries and falls back to HTML/lite backends on rate-limit.
        """
        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _ddgs_text, query, max_results)
        log.debug("web_search.completed", query=query[:60], results=len(results))
        return results

    @staticmethod
    async def search_news(query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """
        Search DuckDuckGo news for the given query.
        Returns list of {title, url, snippet, date, source} dicts.
        """
        def _sync_news() -> list[dict[str, Any]]:
            try:
                from duckduckgo_search import DDGS
                results = []
                with DDGS() as ddgs:
                    for r in ddgs.news(query, max_results=max_results):
                        results.append({
                            "title": r.get("title", ""),
                            "url": r.get("url", ""),
                            "snippet": r.get("body", ""),
                            "date": r.get("date", ""),
                            "source": r.get("source", ""),
                        })
                return results
            except Exception as exc:
                log.error("web_search_news.failed", query=query, error=str(exc))
                return []

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(None, _sync_news)
        log.debug("web_search_news.completed", query=query[:60], results=len(results))
        return results
