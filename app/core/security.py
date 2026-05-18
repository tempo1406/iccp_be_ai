from __future__ import annotations

import hashlib
import json
from typing import Optional

import structlog

from app.core.config import settings
from app.core.exceptions import UnauthorizedException
from app.schemas.common import TokenPayload

log = structlog.get_logger(__name__)

_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _token_cache_key(token: str) -> str:
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"token:{digest}"


async def introspect_token(token: str, organization_id: Optional[str] = None) -> TokenPayload:
    """
    Validate JWT by calling be_core introspect endpoint via BeCoreClient.
    Results are cached in Redis for TOKEN_CACHE_TTL seconds to avoid
    hitting be_core on every single request.

    organization_id comes from the x-organization-id request header and is
    NOT cached — it's overlaid onto the cached payload on each call.
    """
    redis = _get_redis()
    cache_key = _token_cache_key(token)

    # ── 1. Redis cache hit ─────────────────────────────────────────────────
    cached = await redis.get(cache_key)
    if cached:
        try:
            payload = TokenPayload(**json.loads(cached))
            if organization_id:
                payload = payload.model_copy(update={"organization_id": organization_id})
            log.debug("security.token_cache_hit", user_id=payload.user_id)
            return payload
        except Exception:
            pass  # Corrupt cache entry — fall through to network call

    # ── 2. Call be_core introspect via BeCoreClient ────────────────────────
    from app.clients.be_core_client import BeCoreClient
    data = await BeCoreClient.introspect_token(token)

    user_id = data.get("userId") or data.get("user_id") or data.get("sub")
    if not user_id:
        raise UnauthorizedException("Token introspection response missing user_id")

    payload = TokenPayload(
        user_id=user_id,
        email=data.get("email"),
        first_name=data.get("firstName") or data.get("first_name"),
        last_name=data.get("lastName") or data.get("last_name"),
        avatar_url=data.get("avatarUrl") or data.get("avatar_url"),
        organization_id=organization_id or data.get("organizationId") or data.get("organization_id"),
        roles=data.get("roles", []),
        role_ids=data.get("roleIds") or data.get("role_ids") or [],
        project_ids=data.get("projectIds") or data.get("project_ids") or [],
        is_active=data.get("isActive", data.get("is_active", True)),
        is_verified=data.get("isVerified", data.get("is_verified", True)),
    )

    # ── 3. Cache payload WITHOUT org_id (org_id is per-request from header) ─
    cache_data = payload.model_dump()
    cache_data["organization_id"] = None
    await redis.setex(cache_key, settings.TOKEN_CACHE_TTL, json.dumps(cache_data))

    log.debug("security.token_introspected", user_id=payload.user_id)

    return payload


def verify_internal_key(api_key: str) -> bool:
    """Verify internal service-to-service API key."""
    return api_key == settings.INTERNAL_API_KEY
