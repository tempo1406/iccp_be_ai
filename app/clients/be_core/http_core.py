from __future__ import annotations

from typing import Any, Optional

import httpx
import structlog

from app.core.config import settings
from app.core.exceptions import BeCoreClientException

log = structlog.get_logger(__name__)

_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)
_RETRY_STATUS = {500, 502, 503, 504}
_MAX_RETRIES = 3


class BeCoreHttpCore:
    _client: Optional[httpx.AsyncClient] = None

    @classmethod
    async def initialize(cls) -> None:
        cls._client = httpx.AsyncClient(
            base_url=settings.BE_CORE_BASE_URL,
            timeout=_TIMEOUT,
        )
        log.info("be_core_client.initialized", base_url=settings.BE_CORE_BASE_URL)

    @classmethod
    async def close(cls) -> None:
        if cls._client:
            await cls._client.aclose()
            cls._client = None

    @classmethod
    def _get_client(cls) -> httpx.AsyncClient:
        if cls._client is None:
            raise BeCoreClientException("BeCoreClient not initialized")
        return cls._client

    @staticmethod
    def _unwrap_api_response(body: dict[str, Any]) -> dict[str, Any]:
        """
        Unwrap be_core ApiResponseDto payload.
        Shape: { statusCode, message, data }.
        be_core returns data as either a dict or a plain list depending on the endpoint.
        """
        if isinstance(body, dict) and "data" in body:
            data = body.get("data")
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                # Some endpoints (projects, tasks) return data as a plain array
                return {"items": data, "total": len(data)}
            return {}
        return body

    @classmethod
    async def _request(
        cls,
        method: str,
        path: str,
        include_internal_key: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute HTTP request with retry on transient errors."""
        client = cls._get_client()
        last_exc: Optional[Exception] = None

        request_headers = dict(kwargs.pop("headers", {}) or {})
        if include_internal_key:
            request_headers["X-Internal-Key"] = settings.INTERNAL_API_KEY
        if request_headers:
            kwargs["headers"] = request_headers

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = await client.request(method, path, **kwargs)
                if response.status_code in _RETRY_STATUS and attempt < _MAX_RETRIES:
                    log.warning(
                        "be_core_client.retry",
                        method=method,
                        path=path,
                        status=response.status_code,
                        attempt=attempt,
                    )
                    continue
                if response.status_code >= 400:
                    raise BeCoreClientException(
                        message=f"be_core API error: {method} {path} -> {response.status_code}",
                        status_code=response.status_code,
                        detail=response.text,
                    )
                return response.json() if response.content else {}
            except BeCoreClientException:
                raise
            except Exception as exc:
                last_exc = exc
                log.warning(
                    "be_core_client.request_failed",
                    method=method,
                    path=path,
                    attempt=attempt,
                    error=str(exc),
                )

        raise BeCoreClientException(
            f"be_core request failed after {_MAX_RETRIES} attempts: {last_exc}"
        )
