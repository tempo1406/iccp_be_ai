import asyncio
import logging

from celery import Celery
from celery.signals import worker_init

from app.core.config import settings

log = logging.getLogger(__name__)

celery_app = Celery(
    "iccp_ai",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.workers.tasks.ingest_tasks",
        "app.workers.tasks.analytics_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.workers.tasks.ingest_tasks.*": {"queue": "ingest"},
        "app.workers.tasks.analytics_tasks.*": {"queue": "analytics"},
    },
    task_default_queue="ingest",
    broker_connection_retry_on_startup=True,
)


@worker_init.connect
def on_worker_init(**kwargs):
    """
    Initialize shared services once at Celery worker startup.
    This eliminates 100–500 ms of per-task initialization overhead
    (Pinecone, MongoDB, BeCoreClient HTTP client).
    """
    async def _init():
        from app.clients.be_core_client import BeCoreClient
        from app.db.mongodb import init_mongodb
        from app.db.seeds.ai_model_config_seed import seed_ai_model_configs_if_missing
        from app.services.pinecone_service import PineconeService

        await PineconeService.initialize()
        await BeCoreClient.initialize()
        await init_mongodb()
        await seed_ai_model_configs_if_missing()
        log.info("[celery_worker] services initialized at startup")

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_init())
    except Exception as exc:
        log.error(f"[celery_worker] startup initialization failed: {exc}")
        # Don't raise — let tasks fall back to per-task init (still idempotent)
