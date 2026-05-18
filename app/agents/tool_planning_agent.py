from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agents.base import AgentInput, AgentOutput, BaseAgent
from app.core.exceptions import LLMException
from app.schemas.ai_model_config import AIModelPurpose
from app.services.llm_service import ChatMessage, LLMService
from app.tools.registry import TOOL_REGISTRY, get_tool_schema

log = structlog.get_logger(__name__)


@dataclass
class ToolPlanningInput(AgentInput):
    message: str = ""
    context_scope: str = "organization"
    context_id: str | None = None
    allowed_tools: list[str] = field(default_factory=list)


@dataclass
class ToolDecision:
    tool_name: str
    params: dict[str, Any]


@dataclass
class ToolPlanningOutput(AgentOutput):
    decision: ToolDecision | None = None
    raw_content: str = ""


class ToolPlanningAgent(BaseAgent):
    """
    Uses LLM function calling (bind_tools) to decide which tool to call
    and extracts structured parameters.
    """

    async def run(self, input: AgentInput) -> ToolPlanningOutput:
        assert isinstance(input, ToolPlanningInput), "Expected ToolPlanningInput"

        if not input.allowed_tools:
            log.warning(
                "tool_planning.no_allowed_tools",
                trace_id=input.trace_id,
                context_scope=input.context_scope,
                context_id=input.context_id,
            )
            return ToolPlanningOutput(
                success=False,
                error="No tools available for this conversation",
            )

        log.info(
            "tool_planning.started",
            trace_id=input.trace_id,
            organization_id=input.organization_id,
            context_scope=input.context_scope,
            context_id=input.context_id,
            allowed_tools=input.allowed_tools,
            message_preview=input.message[:120],
        )
        tools = self._build_tool_definitions(input.allowed_tools)
        available_tools = ", ".join(input.allowed_tools)
        messages = [
            ChatMessage(
                role="system",
                content=(
                    "You are a smart tool planner for an enterprise project management chatbot.\n\n"
                    "Your job is to analyze the user's request and select the MOST APPROPRIATE tool "
                    "from the available tools, extracting correct parameters.\n\n"
                    "RULES:\n"
                    "1. Always prefer TOOL_QUERY actions over generic chat responses.\n"
                    "2. If the user asks about tasks, projects, tickets, reports, or documents — "
                    "   you MUST select a tool, not decline.\n"
                    "3. If project_id is missing but the tool requires it, still call the tool. "
                    "   The executor will auto-resolve the project if possible.\n"
                    "4. Use the user's ID for assignee filtering when appropriate.\n"
                    "5. For Vietnamese queries, understand synonyms: "
                    "   'dự án' = project, 'công việc'/'việc' = task, "
                    "   'phiếu'/'đơn' = ticket, 'báo cáo' = report.\n"
                    f"6. You may ONLY use these tools: {available_tools}.\n\n"
                    "Be precise with parameters. Do not make up IDs."
                ),
            ),
            ChatMessage(role="human", content=input.message),
        ]

        try:
            response = await LLMService.ainvoke_with_tools(
                messages,
                tools=tools,
                organization_id=input.organization_id,
                purpose=AIModelPurpose.TOOL_PLANNING,
            )
        except LLMException as exc:
            log.error("tool_planning.llm_failed", error=str(exc), trace_id=input.trace_id)
            return ToolPlanningOutput(
                success=False,
                error=f"Tool planning failed: {exc}",
            )

        if not response.tool_calls:
            log.warning(
                "tool_planning.no_tool_call",
                allowed_tools=input.allowed_tools,
                content=response.content[:200],
                trace_id=input.trace_id,
            )
            return ToolPlanningOutput(
                success=False,
                error="No tool call detected in LLM response",
                raw_content=response.content,
            )

        # Take the first tool call
        tc = response.tool_calls[0]
        tool_name = tc.get("name", "")
        params = tc.get("args", {})

        if tool_name not in input.allowed_tools:
            return ToolPlanningOutput(
                success=False,
                error=f"Tool `{tool_name}` is not allowed in this conversation",
            )

        log.info(
            "tool_planning.decision",
            tool_name=tool_name,
            params_keys=list(params.keys()),
            trace_id=input.trace_id,
        )

        # Validate against registered schema
        schema_cls = get_tool_schema(tool_name)
        if schema_cls is None:
            return ToolPlanningOutput(
                success=False,
                error=f"LLM chose unknown tool: {tool_name}",
            )

        try:
            validated = schema_cls(**params)
            # Inject inferred context if missing
            params_out = validated.model_dump()
            if not params_out.get("project_id") and input.context_id and input.context_scope == "project":
                params_out["project_id"] = input.context_id

            log.info(
                "tool_planning.validation_succeeded",
                trace_id=input.trace_id,
                tool_name=tool_name,
                params_keys=sorted(params_out.keys()),
            )

            return ToolPlanningOutput(
                success=True,
                decision=ToolDecision(tool_name=tool_name, params=params_out),
                raw_content=response.content,
            )
        except Exception as exc:
            log.warning(
                "tool_planning.validation_failed",
                tool_name=tool_name,
                params=params,
                error=str(exc),
                trace_id=input.trace_id,
            )
            return ToolPlanningOutput(
                success=False,
                error=f"Invalid params for {tool_name}: {exc}",
            )

    @staticmethod
    def _build_tool_definitions(allowed_tools: list[str]) -> list[dict[str, Any]]:
        """Build LangChain-compatible tool definitions from registry."""
        tools = []
        for tool_name in allowed_tools:
            meta = TOOL_REGISTRY.get(tool_name)
            if meta is None:
                continue
            schema = meta.schema.model_json_schema()
            # Clean up schema for LLM consumption
            schema.pop("title", None)
            tools.append({
                "type": "function",
                "function": {
                    "name": meta.name,
                    "description": meta.description,
                    "parameters": schema,
                },
            })
        return tools
