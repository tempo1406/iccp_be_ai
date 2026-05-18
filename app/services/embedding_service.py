from __future__ import annotations

import hashlib
import json
from typing import Optional

import httpx
import structlog

from app.core.config import settings
from app.core.exceptions import EmbeddingException
from app.schemas.ai_model_config import AIModelPurpose, AIModelProvider
from app.services.ai_model_config_service import AIModelConfigService, ResolvedAIModelConfig

log = structlog.get_logger(__name__)

_BATCH_SIZE = 50
_GEMINI_EMBED_BASE_URLS = [
    "https://generativelanguage.googleapis.com/v1beta",
    "https://generativelanguage.googleapis.com/v1",
]
_GEMINI_EMBED_MODEL_FALLBACKS = [
    "models/gemini-embedding-001",
    "models/gemini-embedding-2-preview",
]
_redis_client = None


def _get_redis():
    global _redis_client
    if _redis_client is None:
        import redis.asyncio as aioredis
        _redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


def _cache_key(model_name: str, text: str, output_dimension: int | None = None) -> str:
    model_slug = model_name.replace("/", "-").replace(":", "-")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if output_dimension:
        return f"emb:{model_slug}:dim{output_dimension}:{digest}"
    return f"emb:{model_slug}:{digest}"


async def _gemini_embed_batch(
    texts: list[str],
    runtime: ResolvedAIModelConfig,
    output_dimension: int | None,
) -> list[list[float]]:
    requested_model = runtime.model_name.strip()
    if not requested_model.startswith("models/"):
        requested_model = f"models/{requested_model}"

    model_candidates: list[str] = [requested_model]
    for fallback_model in _GEMINI_EMBED_MODEL_FALLBACKS:
        if fallback_model not in model_candidates:
            model_candidates.append(fallback_model)

    candidate_bases: list[str] = []
    if runtime.api_base_url:
        candidate_bases.append(runtime.api_base_url.rstrip("/"))
    for base in _GEMINI_EMBED_BASE_URLS:
        if base not in candidate_bases:
            candidate_bases.append(base)

    last_404_detail: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        data = None
        selected_model = requested_model
        for model in model_candidates:
            payload = {
                "requests": [
                    {
                        "model": model,
                        "content": {"parts": [{"text": text}]},
                        **({"outputDimensionality": output_dimension} if output_dimension else {}),
                    }
                    for text in texts
                ]
            }

            for base_url in candidate_bases:
                url = f"{base_url}/{model}:batchEmbedContents"
                resp = await client.post(
                    url,
                    params={"key": runtime.api_key},
                    json=payload,
                )

                if resp.status_code == 404:
                    last_404_detail = resp.text
                    continue

                if resp.status_code >= 400:
                    raise EmbeddingException(
                        f"Gemini embedding API error {resp.status_code}: {resp.text}"
                    )

                data = resp.json()
                selected_model = model
                log.debug(
                    "embedding.gemini_endpoint_selected",
                    url=url,
                    model=selected_model,
                    requested_model=requested_model,
                )
                break

            if data is not None:
                break

        if data is None:
            detail = last_404_detail or "No successful Gemini embedding endpoint"
            raise EmbeddingException(f"Gemini embedding API error 404: {detail}")

        if selected_model != requested_model:
            log.warning(
                "embedding.gemini_model_fallback",
                requested_model=requested_model,
                selected_model=selected_model,
            )

    embeddings = data.get("embeddings", [])
    if len(embeddings) != len(texts):
        raise EmbeddingException(
            f"Embedding count mismatch: expected {len(texts)}, got {len(embeddings)}"
        )
    return [embedding["values"] for embedding in embeddings]


class EmbeddingService:
    @classmethod
    async def embed_one(
        cls,
        text: str,
        *,
        organization_id: Optional[str] = None,
    ) -> list[float]:
        results = await cls.embed_batch([text], organization_id=organization_id)
        return results[0]

    @classmethod
    async def embed_batch(
        cls,
        texts: list[str],
        *,
        organization_id: Optional[str] = None,
    ) -> list[list[float]]:
        if not texts:
            return []

        runtime = await AIModelConfigService.resolve_model_config(
            purpose=AIModelPurpose.EMBEDDING,
            organization_id=organization_id,
        )
        if runtime.provider != AIModelProvider.GEMINI:
            raise EmbeddingException(
                f"Embedding provider '{runtime.provider.value}' is not supported"
            )

        output_dimension = settings.GEMINI_EMBEDDING_OUTPUT_DIMENSION or None
        redis = _get_redis()
        keys = [_cache_key(runtime.model_name, text, output_dimension) for text in texts]
        cached_values: list[Optional[str]] = await redis.mget(*keys)

        embeddings: list[list[float] | None] = [None] * len(texts)
        uncached_indices: list[int] = []
        uncached_texts: list[str] = []

        for index, cached in enumerate(cached_values):
            if cached:
                embeddings[index] = json.loads(cached)
            else:
                uncached_indices.append(index)
                uncached_texts.append(texts[index])

        cache_hits = len(texts) - len(uncached_texts)

        if uncached_texts:
            new_vectors: list[list[float]] = []
            try:
                for batch_start in range(0, len(uncached_texts), _BATCH_SIZE):
                    batch = uncached_texts[batch_start: batch_start + _BATCH_SIZE]
                    vectors = await _gemini_embed_batch(batch, runtime, output_dimension)
                    new_vectors.extend(vectors)
            except EmbeddingException:
                raise
            except Exception as exc:
                raise EmbeddingException(f"Gemini embedding failed: {exc}") from exc

            pipe = redis.pipeline(transaction=False)
            for index, vector in enumerate(new_vectors):
                original_idx = uncached_indices[index]
                embeddings[original_idx] = vector
                pipe.setex(
                    keys[original_idx],
                    settings.EMBEDDING_CACHE_TTL,
                    json.dumps(vector),
                )
            await pipe.execute()

        log.debug(
            "embedding.batch_complete",
            provider=runtime.provider.value,
            model=runtime.model_name,
            total=len(texts),
            cache_hits=cache_hits,
            fetched=len(uncached_texts),
        )

        return [embedding if embedding is not None else [] for embedding in embeddings]

