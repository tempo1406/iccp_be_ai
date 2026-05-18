from __future__ import annotations

from dataclasses import dataclass

import structlog

from app.core.config import settings
from app.db.mongodb import get_database
from app.db.repositories.ai_model_config_repo import AIModelConfigRepository
from app.db.schemas.ai_model_config import AIModelConfigSchema
from app.schemas.ai_model_config import AIModelProvider, AIModelPurpose, AIModelTransport

log = structlog.get_logger(__name__)

_DEFAULT_BEEKNOEE_SEED_MODELS = [
    "openai/gpt-oss-120b",
    "MiniMax-M2.5",
    "glm-4.7-flash",
    "grok-4-1-fast",
]

_FULL_NON_EMBEDDING_PURPOSES = [
    AIModelPurpose.CHAT_COMPLETION,
    AIModelPurpose.INTENT_ROUTER,
    AIModelPurpose.SUGGESTION,
    AIModelPurpose.CONTENT_MODERATION,
    AIModelPurpose.LANDING_PAGE_GENERATION,
    AIModelPurpose.LANDING_PAGE_STATUS,
]


@dataclass
class _SeedSpec:
    provider: AIModelProvider
    transport: AIModelTransport
    purpose_codes: list[AIModelPurpose]
    model_name: str
    model_display_name: str
    api_key: str
    api_base_url: str | None = None
    sdk_name: str | None = None
    description: str | None = None
    is_enabled: bool = True
    applies_to_all_plans: bool = True
    allowed_subscription_plan_codes: list[str] | None = None
    priority: int = 100


def _mask_api_key(api_key: str) -> str:
    cleaned = api_key.strip()
    if len(cleaned) <= 8:
        return "*" * len(cleaned)
    return f"{cleaned[:4]}...{cleaned[-4:]}"


def _build_seed_specs() -> list[_SeedSpec]:
    specs: list[_SeedSpec] = []

    if settings.GEMINI_API_KEY:
        specs.append(
            _SeedSpec(
                provider=AIModelProvider.GEMINI,
                transport=AIModelTransport.SDK,
                purpose_codes=list(_FULL_NON_EMBEDDING_PURPOSES),
                model_name=settings.GEMINI_CHAT_MODEL,
                model_display_name=f"Gemini 3.1-flash-lite-preview",
                api_key=settings.GEMINI_API_KEY,
                sdk_name="langchain_google_genai",
                description="Seeded Gemini config with all non-embedding purposes.",
                priority=10,
            )
        )
        specs.append(
            _SeedSpec(
                provider=AIModelProvider.GEMINI,
                transport=AIModelTransport.HTTP_API,
                purpose_codes=[AIModelPurpose.EMBEDDING],
                model_name=settings.GEMINI_EMBEDDING_MODEL,
                model_display_name=f"Gemini text-embedding-004",
                api_key=settings.GEMINI_API_KEY,
                api_base_url="https://generativelanguage.googleapis.com/v1beta",
                description="Seeded Gemini embedding config.",
                priority=5,
            )
        )

    if settings.beeknoee_api_key:
        # Seed a default Beeknoee config for landing page purposes.
        specs.append(
            _SeedSpec(
                provider=AIModelProvider.BEEKNOEE,
                transport=AIModelTransport.HTTP_API,
                purpose_codes=list(_FULL_NON_EMBEDDING_PURPOSES),
                model_name=settings.beeknoee_model,
                model_display_name=settings.beeknoee_model,
                api_key=settings.beeknoee_api_key,
                api_base_url=settings.beeknoee_api_base,
                description="Seeded Beeknoee config with all non-embedding purposes.",
                priority=10,
            )
        )

        primary_model_name = settings.beeknoee_model.strip().lower()
        model_names = [
            name
            for name in _DEFAULT_BEEKNOEE_SEED_MODELS
            if name.strip().lower() != primary_model_name
        ]
        # Keep unique while preserving order.
        deduped_model_names: list[str] = []
        seen: set[str] = set()
        for name in model_names:
            if name not in seen:
                seen.add(name)
                deduped_model_names.append(name)

        for idx, model_name in enumerate(deduped_model_names, start=1):
            specs.append(
                _SeedSpec(
                    provider=AIModelProvider.BEEKNOEE,
                    transport=AIModelTransport.HTTP_API,
                    purpose_codes=list(_FULL_NON_EMBEDDING_PURPOSES),
                    model_name=model_name,
                    model_display_name=model_name,
                    api_key=settings.beeknoee_api_key,
                    api_base_url=settings.beeknoee_api_base,
                    description="Seeded Beeknoee config with all non-embedding purposes.",
                    priority=20 + idx,
                )
            )

    return specs


def _signature_from_schema(config: AIModelConfigSchema) -> tuple:
    return (
        config.provider.value,
        config.transport.value,
        tuple(sorted(p.value for p in config.purpose_codes)),
        config.model_name,
        config.api_base_url,
        config.applies_to_all_plans,
        tuple(sorted(config.allowed_subscription_plan_codes)),
    )


def _signature_from_spec(spec: _SeedSpec) -> tuple:
    return (
        spec.provider.value,
        spec.transport.value,
        tuple(sorted(p.value for p in spec.purpose_codes)),
        spec.model_name,
        spec.api_base_url,
        spec.applies_to_all_plans,
        tuple(sorted(spec.allowed_subscription_plan_codes or [])),
    )


def _identity_from_schema(config: AIModelConfigSchema) -> tuple:
    return (
        config.provider.value,
        config.transport.value,
        config.model_name,
        config.applies_to_all_plans,
        tuple(sorted(config.allowed_subscription_plan_codes)),
    )


def _identity_from_spec(spec: _SeedSpec) -> tuple:
    return (
        spec.provider.value,
        spec.transport.value,
        spec.model_name,
        spec.applies_to_all_plans,
        tuple(sorted(spec.allowed_subscription_plan_codes or [])),
    )


async def seed_ai_model_configs_if_missing() -> None:
    if not settings.ENABLE_AI_MODEL_CONFIG_SEED:
        log.info("ai_model_config.seed.disabled")
        return

    repo = AIModelConfigRepository(get_database())
    existing = await repo.list_configs(include_deleted=True)

    # Keep only one active config per identity; soft-delete duplicates.
    active_grouped: dict[tuple, list[AIModelConfigSchema]] = {}
    for item in existing:
        if item.deleted_at is not None:
            continue
        active_grouped.setdefault(_identity_from_schema(item), []).append(item)

    duplicate_soft_deleted = 0
    for _, items in active_grouped.items():
        if len(items) <= 1:
            continue

        items_sorted = sorted(
            items,
            key=lambda cfg: (cfg.priority, -cfg.updated_at.timestamp(), -cfg.created_at.timestamp()),
        )
        for duplicated in items_sorted[1:]:
            deleted = await repo.soft_delete(duplicated.id, updated_by="system-seed")
            if deleted:
                duplicate_soft_deleted += 1

    if duplicate_soft_deleted > 0:
        log.info("ai_model_config.seed.duplicate_soft_deleted", count=duplicate_soft_deleted)
        existing = await repo.list_configs(include_deleted=True)

    existing_signatures = {_signature_from_schema(item) for item in existing}
    existing_by_identity = {
        _identity_from_schema(item): item for item in existing if item.deleted_at is None
    }

    specs = _build_seed_specs()
    if not specs:
        log.info("ai_model_config.seed.skipped_no_provider_keys")
        return

    created = 0
    updated = 0
    skipped = 0

    for spec in specs:
        signature = _signature_from_spec(spec)
        if signature in existing_signatures:
            skipped += 1
            continue

        identity = _identity_from_spec(spec)
        existing_item = existing_by_identity.get(identity)
        if existing_item:
            updated_item = await repo.update(
                existing_item.id,
                {
                    "purpose_codes": spec.purpose_codes,
                    "model_display_name": spec.model_display_name,
                    "api_key": spec.api_key,
                    "api_key_masked": _mask_api_key(spec.api_key),
                    "api_base_url": spec.api_base_url,
                    "sdk_name": spec.sdk_name,
                    "description": spec.description,
                    "is_enabled": spec.is_enabled,
                    "priority": spec.priority,
                    "updated_by": "system-seed",
                },
            )
            if updated_item:
                existing_signatures.add(_signature_from_schema(updated_item))
                existing_by_identity[identity] = updated_item
                updated += 1
                continue

        config = AIModelConfigSchema(
            provider=spec.provider,
            transport=spec.transport,
            purpose_codes=spec.purpose_codes,
            model_name=spec.model_name,
            model_display_name=spec.model_display_name,
            api_key=spec.api_key,
            api_key_masked=_mask_api_key(spec.api_key),
            api_base_url=spec.api_base_url,
            sdk_name=spec.sdk_name,
            description=spec.description,
            is_enabled=spec.is_enabled,
            applies_to_all_plans=spec.applies_to_all_plans,
            allowed_subscription_plan_codes=spec.allowed_subscription_plan_codes or [],
            priority=spec.priority,
            created_by="system-seed",
            updated_by="system-seed",
        )
        await repo.create(config)
        existing_signatures.add(signature)
        existing_by_identity[identity] = config
        created += 1

    log.info(
        "ai_model_config.seed.completed",
        created=created,
        updated=updated,
        skipped=skipped,
        total_seed_specs=len(specs),
    )
