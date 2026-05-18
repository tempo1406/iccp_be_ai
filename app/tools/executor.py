from __future__ import annotations

import time
from typing import Any

import structlog

from app.core.exceptions import BeCoreClientException

from .base import ToolContext, ToolResult
from .registry import TOOL_REGISTRY

log = structlog.get_logger(__name__)


class ToolExecutor:
    """
    Dispatches tool execution to the appropriate domain executor.
    All calls forward the user JWT so be_core handles RBAC natively.
    """

    @classmethod
    async def execute(
        cls,
        tool_name: str,
        params: dict[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        meta = TOOL_REGISTRY.get(tool_name)
        if not meta:
            return ToolResult(success=False, tool=tool_name, error=f"Unknown tool: {tool_name}")

        started_at = time.monotonic()

        log.info(
            "tool_executor.start",
            tool_name=tool_name,
            toolset=meta.toolset,
            action_type=meta.action_type,
            params_keys=sorted(params.keys()),
            trace_id=ctx.trace_id,
        )

        # Validate params against schema
        validated = meta.schema(**params)

        # Get executor method
        executor = meta.executor_class
        handler = getattr(executor, meta.executor_method, None)
        if handler is None:
            return ToolResult(
                success=False,
                tool=tool_name,
                error=f"No handler {meta.executor_method} in {executor.__name__}",
            )

        try:
            data = await handler(validated, ctx)
            log.info(
                "tool_executor.complete",
                tool_name=tool_name,
                trace_id=ctx.trace_id,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            return ToolResult(success=True, tool=tool_name, data=data)
        except BeCoreClientException as exc:
            log.error(
                "tool_executor.becore_error",
                tool_name=tool_name,
                error=str(exc),
                trace_id=ctx.trace_id,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            return ToolResult(
                success=False,
                tool=tool_name,
                error=f"Upstream error: {exc.message}",
            )
        except Exception as exc:
            log.error(
                "tool_executor.error",
                tool_name=tool_name,
                error=str(exc),
                trace_id=ctx.trace_id,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            return ToolResult(success=False, tool=tool_name, error=str(exc))
