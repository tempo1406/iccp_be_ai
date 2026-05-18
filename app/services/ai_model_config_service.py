from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import uuid

import structlog
from fastapi import HTTPException, status

from app.clients.be_core_client import BeCoreClient
from app.core.config import settings
from app.db.mongodb import get_database
from app.db.repositories.ai_model_config_repo import AIModelConfigRepository
from app.db.schemas.ai_model_config import AIModelConfigSchema
from app.schemas.ai_model_config import (
    AIModelConfigCreateRequest,
    AIModelOptionResponse,
    AIModelConfigResponse,
    AIModelConfigUpdateRequest,
    AIModelProvider,
    AIModelPurpose,
    AIModelTransport,
)
from app.schemas.common import TokenPayload

log = structlog.get_logger(__name__)

_LANDING_PAGE_PURPOSES = {
    AIModelPurpose.LANDING_PAGE_GENERATION,
    AIModelPurpose.LANDING_PAGE_STATUS,
}


@dataclass
class ResolvedAIModelConfig:
    provider: AIModelProvider
    transport: AIModelTransport
    purpose_code: AIModelPurpose
    model_name: str
    model_display_name: str
    api_key: str
    api_base_url: Optional[str] = None
    sdk_name: Optional[str] = None
    source: str = "mongo"
    subscription_plan_code: Optional[str] = None


class AIModelConfigService:
    @classmethod
    def _repo(cls) -> AIModelConfigRepository:
        return AIModelConfigRepository(get_database())

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        cleaned = api_key.strip()
        if len(cleaned) <= 8:
            return "*" * len(cleaned)
        return f"{cleaned[:4]}...{cleaned[-4:]}"

    @staticmethod
    def _normalize_plan_codes(plan_codes: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()

        for plan_code in plan_codes:
            code = plan_code.strip().lower()
            if code and code not in seen:
                seen.add(code)
                normalized.append(code)

        return normalized

    @classmethod
    def _to_response(cls, config: AIModelConfigSchema) -> AIModelConfigResponse:
        return AIModelConfigResponse(
            id=config.id,
            provider=config.provider,
            transport=config.transport,
            purpose_codes=config.purpose_codes,
            model_name=config.model_name,
            model_display_name=config.model_display_name,
            api_key_masked=config.api_key_masked,
            api_base_url=config.api_base_url,
            sdk_name=config.sdk_name,
            description=config.description,
            is_enabled=config.is_enabled,
            applies_to_all_plans=config.applies_to_all_plans,
            allowed_subscription_plan_codes=config.allowed_subscription_plan_codes,
            priority=config.priority,
            is_deleted=config.deleted_at is not None,
            created_by=config.created_by,
            updated_by=config.updated_by,
            created_at=config.created_at,
            updated_at=config.updated_at,
            deleted_at=config.deleted_at,
        )

    @classmethod
    async def create_config(
        cls,
        body: AIModelConfigCreateRequest,
        current_user: TokenPayload,
    ) -> AIModelConfigResponse:
        config = AIModelConfigSchema(
            provider=body.provider,
            transport=body.transport,
            purpose_codes=body.purpose_codes,
            model_name=body.model_name,
            model_display_name=body.model_display_name,
            api_key=body.api_key,
            api_key_masked=cls._mask_api_key(body.api_key),
            api_base_url=body.api_base_url,
            sdk_name=body.sdk_name,
            description=body.description,
            is_enabled=body.is_enabled,
            applies_to_all_plans=body.applies_to_all_plans,
            allowed_subscription_plan_codes=cls._normalize_plan_codes(body.allowed_subscription_plan_codes),
            priority=body.priority,
            created_by=current_user.user_id,
            updated_by=current_user.user_id,
        )
        saved = await cls._repo().create(config)
        return cls._to_response(saved)

    @classmethod
    async def list_configs(
        cls,
        *,
        provider: Optional[AIModelProvider] = None,
        purpose: Optional[AIModelPurpose] = None,
        is_enabled: Optional[bool] = None,
        include_deleted: bool = False,
    ) -> list[AIModelConfigResponse]:
        items = await cls._repo().list_configs(
            provider=provider.value if provider else None,
            purpose=purpose.value if purpose else None,
            is_enabled=is_enabled,
            include_deleted=include_deleted,
        )
        return [cls._to_response(item) for item in items]

    @classmethod
    async def list_available_options_for_user(
        cls,
        *,
        organization_id: Optional[str],
        purpose: Optional[AIModelPurpose] = None,
    ) -> list[AIModelOptionResponse]:
        subscription_plan_code = await cls._get_subscription_plan_code(organization_id)
        configs = await cls._repo().list_available_for_subscription(
            purpose=purpose.value if purpose else None,
            subscription_plan_code=subscription_plan_code,
        )
        return [AIModelOptionResponse(id=config.id, name=config.model_display_name) for config in configs]

    @classmethod
    async def get_config(
        cls,
        config_id: str,
        *,
        include_deleted: bool = False,
    ) -> AIModelConfigResponse:
        config = await cls._repo().get_by_id(config_id, include_deleted=include_deleted)
        if not config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI model config not found",
            )

        return cls._to_response(config)

    @classmethod
    async def update_config(
        cls,
        config_id: str,
        body: AIModelConfigUpdateRequest,
        current_user: TokenPayload,
    ) -> AIModelConfigResponse:
        repo = cls._repo()
        existing = await repo.get_by_id(config_id)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI model config not found",
            )

        update_data = body.model_dump(exclude_unset=True)
        merged = {
            "provider": update_data.get("provider", existing.provider),
            "transport": update_data.get("transport", existing.transport),
            "purpose_codes": update_data.get("purpose_codes", existing.purpose_codes),
            "model_name": update_data.get("model_name", existing.model_name),
            "model_display_name": update_data.get("model_display_name", existing.model_display_name),
            "api_key": update_data.get("api_key", existing.api_key),
            "api_base_url": update_data.get("api_base_url", existing.api_base_url),
            "sdk_name": update_data.get("sdk_name", existing.sdk_name),
            "description": update_data.get("description", existing.description),
            "is_enabled": update_data.get("is_enabled", existing.is_enabled),
            "applies_to_all_plans": update_data.get("applies_to_all_plans", existing.applies_to_all_plans),
            "allowed_subscription_plan_codes": update_data.get(
                "allowed_subscription_plan_codes",
                existing.allowed_subscription_plan_codes,
            ),
            "priority": update_data.get("priority", existing.priority),
        }
        validated = AIModelConfigCreateRequest(**merged)

        payload = {
            "provider": validated.provider,
            "transport": validated.transport,
            "purpose_codes": validated.purpose_codes,
            "model_name": validated.model_name,
            "model_display_name": validated.model_display_name,
            "api_key": validated.api_key,
            "api_key_masked": cls._mask_api_key(validated.api_key),
            "api_base_url": validated.api_base_url,
            "sdk_name": validated.sdk_name,
            "description": validated.description,
            "is_enabled": validated.is_enabled,
            "applies_to_all_plans": validated.applies_to_all_plans,
            "allowed_subscription_plan_codes": cls._normalize_plan_codes(validated.allowed_subscription_plan_codes),
            "priority": validated.priority,
            "updated_by": current_user.user_id,
        }
        updated = await repo.update(config_id, payload)
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI model config not found",
            )

        return cls._to_response(updated)

    @classmethod
    async def soft_delete_config(
        cls,
        config_id: str,
        current_user: TokenPayload,
    ) -> None:
        deleted = await cls._repo().soft_delete(
            config_id,
            updated_by=current_user.user_id,
        )
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI model config not found",
            )

    @classmethod
    async def set_enabled(
        cls,
        config_id: str,
        is_enabled: bool,
        current_user: TokenPayload,
    ) -> AIModelConfigResponse:
        updated = await cls._repo().set_enabled(
            config_id,
            is_enabled,
            updated_by=current_user.user_id,
        )
        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="AI model config not found",
            )

        return cls._to_response(updated)

    @classmethod
    async def resolve_model_config(
        cls,
        *,
        purpose: AIModelPurpose,
        organization_id: Optional[str] = None,
        selected_model_config_id: Optional[str] = None,
        custom_api_key: Optional[str] = None,
    ) -> ResolvedAIModelConfig:
        subscription_plan_code = await cls._get_subscription_plan_code(organization_id)

        if selected_model_config_id:
            selected = await cls._resolve_selected_model_config(
                config_id=selected_model_config_id,
                purpose=purpose,
                subscription_plan_code=subscription_plan_code,
            )
            return ResolvedAIModelConfig(
                provider=selected.provider,
                transport=selected.transport,
                purpose_code=purpose,
                model_name=selected.model_name,
                model_display_name=selected.model_display_name,
                api_key=custom_api_key if custom_api_key and selected.provider == AIModelProvider.GEMINI else selected.api_key,
                api_base_url=selected.api_base_url,
                sdk_name=selected.sdk_name,
                source="mongo",
                subscription_plan_code=subscription_plan_code,
            )

        resolved = await cls._repo().resolve_for_purpose(
            purpose=purpose.value,
            subscription_plan_code=subscription_plan_code,
        )

        if resolved:
            return ResolvedAIModelConfig(
                provider=resolved.provider,
                transport=resolved.transport,
                purpose_code=purpose,
                model_name=resolved.model_name,
                model_display_name=resolved.model_display_name,
                api_key=custom_api_key if custom_api_key and resolved.provider == AIModelProvider.GEMINI else resolved.api_key,
                api_base_url=resolved.api_base_url,
                sdk_name=resolved.sdk_name,
                source="mongo",
                subscription_plan_code=subscription_plan_code,
            )

        return cls._build_fallback_config(
            purpose=purpose,
            subscription_plan_code=subscription_plan_code,
            custom_api_key=custom_api_key,
        )

    @classmethod
    async def _resolve_selected_model_config(
        cls,
        *,
        config_id: str,
        purpose: AIModelPurpose,
        subscription_plan_code: Optional[str],
    ) -> AIModelConfigSchema:
        selected = await cls._repo().get_by_id(config_id, include_deleted=True)
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Selected AI model config not found",
            )

        if selected.deleted_at is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected AI model config has been deleted",
            )

        if not selected.is_enabled:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected AI model config is disabled",
            )

        if purpose not in selected.purpose_codes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Selected AI model config does not support this purpose",
            )

        if not selected.applies_to_all_plans:
            if not subscription_plan_code or subscription_plan_code not in selected.allowed_subscription_plan_codes:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Selected AI model config is not available for current subscription",
                )

        return selected

    @classmethod
    async def _get_subscription_plan_code(cls, organization_id: Optional[str]) -> Optional[str]:
        if not organization_id:
            return None

        if not isinstance(organization_id, str):
            log.warning(
                "ai_model_config.invalid_organization_id_type",
                organization_id_type=type(organization_id).__name__,
            )
            return None

        try:
            uuid.UUID(organization_id)
        except ValueError:
            log.warning(
                "ai_model_config.invalid_organization_id_value",
                organization_id=organization_id,
            )
            return None

        try:
            subscription = await BeCoreClient.get_org_subscription_info(organization_id)
            if not subscription or not subscription.plan_code:
                return None
            return subscription.plan_code.strip().lower()
        except Exception as exc:
            log.warning(
                "ai_model_config.billing_lookup_failed",
                organization_id=organization_id,
                error=str(exc),
            )
            return None

    @classmethod
    def _build_fallback_config(
        cls,
        *,
        purpose: AIModelPurpose,
        subscription_plan_code: Optional[str],
        custom_api_key: Optional[str],
    ) -> ResolvedAIModelConfig:
        if purpose in _LANDING_PAGE_PURPOSES and settings.beeknoee_api_key:
            return ResolvedAIModelConfig(
                provider=AIModelProvider.BEEKNOEE,
                transport=AIModelTransport.HTTP_API,
                purpose_code=purpose,
                model_name=settings.beeknoee_model,
                model_display_name=settings.beeknoee_model,
                api_key=settings.beeknoee_api_key,
                api_base_url=settings.beeknoee_api_base,
                source="env",
                subscription_plan_code=subscription_plan_code,
            )

        transport = (
            AIModelTransport.HTTP_API
            if purpose == AIModelPurpose.EMBEDDING
            else AIModelTransport.SDK
        )

        return ResolvedAIModelConfig(
            provider=AIModelProvider.GEMINI,
            transport=transport,
            purpose_code=purpose,
            model_name=(
                settings.GEMINI_EMBEDDING_MODEL
                if purpose == AIModelPurpose.EMBEDDING
                else settings.GEMINI_CHAT_MODEL
            ),
            model_display_name=(
                settings.GEMINI_EMBEDDING_MODEL
                if purpose == AIModelPurpose.EMBEDDING
                else settings.GEMINI_CHAT_MODEL
            ),
            api_key=custom_api_key or settings.GEMINI_API_KEY,
            api_base_url=None,
            sdk_name="langchain_google_genai" if transport == AIModelTransport.SDK else None,
            source="env",
            subscription_plan_code=subscription_plan_code,
        )
