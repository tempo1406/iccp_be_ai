from fastapi import APIRouter
from app.api.v1 import health, ingest, quotas
from app.api.v1 import (
    ai_model_configs,
    ai_models,
    conversations,
    internal_documents,
    landing_pages,
    messages,
)

v1_router = APIRouter()

v1_router.include_router(health.router)
v1_router.include_router(conversations.router, prefix="/api/v1")
v1_router.include_router(messages.router, prefix="/api/v1")
v1_router.include_router(ingest.router, prefix="/api/v1")
v1_router.include_router(internal_documents.router, prefix="/api/v1")
v1_router.include_router(quotas.router, prefix="/api/v1")
v1_router.include_router(landing_pages.router, prefix="/api/v1")
v1_router.include_router(ai_model_configs.router, prefix="/api/v1")
v1_router.include_router(ai_models.router, prefix="/api/v1")
