"""
Prometheus metrics instrumentation for FastAPI.

Install dependency:
    pip install prometheus-fastapi-instrumentator

Usage in main.py:
    from app.core.metrics import setup_metrics
    setup_metrics(app)
"""

from fastapi import FastAPI


def setup_metrics(app: FastAPI) -> None:
    """
    Mount /metrics endpoint and instrument all requests.
    Must be called AFTER all routers are included.
    """
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        instrumentator = Instrumentator(
            should_group_status_codes=True,
            should_ignore_untemplated=True,
            should_instrument_requests_inprogress=True,
            excluded_handlers=["/metrics", "/health", "/docs", "/redoc", "/openapi.json"],
            inprogress_name="http_requests_inprogress",
            inprogress_labels=True,
        )
        instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    except ImportError:
        import structlog

        log = structlog.get_logger(__name__)
        log.warning("prometheus_fastapi_instrumentator not installed; metrics disabled")
