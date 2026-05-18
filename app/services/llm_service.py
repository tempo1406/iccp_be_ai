from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Callable, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import settings
from app.core.exceptions import LLMException
from app.schemas.ai_model_config import AIModelPurpose, AIModelProvider
from app.services.ai_model_config_service import AIModelConfigService, ResolvedAIModelConfig
from app.services.beeknoee_service import BeeknoeeResponse, beeknoee_ainvoke, beeknoee_astream


@dataclass
class LLMResponse:
    content: str
    tokens_used: int
    model: str
    tool_calls: list[dict[str, Any]] = None

    def __post_init__(self):
        if self.tool_calls is None:
            self.tool_calls = []


@dataclass
class ChatMessage:
    role: str
    content: str


class LLMService:
    _gemini_models: dict[tuple[str, str, float, int, bool], ChatGoogleGenerativeAI] = {}

    @classmethod
    def _build_messages(cls, messages: list[ChatMessage]) -> list:
        result = []
        for msg in messages:
            if msg.role == "system":
                result.append(SystemMessage(content=msg.content))
            elif msg.role == "human":
                result.append(HumanMessage(content=msg.content))
            elif msg.role == "ai":
                result.append(AIMessage(content=msg.content))
        return result

    @classmethod
    def _get_gemini_model(
        cls,
        config: ResolvedAIModelConfig,
        *,
        streaming: bool,
        temperature: float,
        max_tokens: int,
    ) -> ChatGoogleGenerativeAI:
        cache_key = (
            config.model_name,
            config.api_key,
            temperature,
            max_tokens,
            streaming,
        )
        if cache_key not in cls._gemini_models:
            cls._gemini_models[cache_key] = ChatGoogleGenerativeAI(
                model=config.model_name,
                api_key=config.api_key,
                temperature=temperature,
                max_tokens=max_tokens,
                streaming=streaming,
            )
        return cls._gemini_models[cache_key]

    @classmethod
    async def ainvoke(
        cls,
        messages: list[ChatMessage],
        *,
        organization_id: Optional[str] = None,
        purpose: AIModelPurpose = AIModelPurpose.CHAT_COMPLETION,
        selected_model_config_id: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        lc_messages = cls._build_messages(messages)
        return await cls.ainvoke_lc_messages(
            lc_messages,
            organization_id=organization_id,
            purpose=purpose,
            selected_model_config_id=selected_model_config_id,
            custom_api_key=custom_api_key,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    @classmethod
    async def astream(
        cls,
        messages: list[ChatMessage],
        *,
        organization_id: Optional[str] = None,
        purpose: AIModelPurpose = AIModelPurpose.CHAT_COMPLETION,
        selected_model_config_id: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        on_complete: Optional[Callable[[LLMResponse], None]] = None,
    ) -> AsyncGenerator[str, None]:
        lc_messages = cls._build_messages(messages)
        async for chunk in cls.astream_lc_messages(
            lc_messages,
            organization_id=organization_id,
            purpose=purpose,
            selected_model_config_id=selected_model_config_id,
            custom_api_key=custom_api_key,
            max_tokens=max_tokens,
            temperature=temperature,
            on_complete=on_complete,
        ):
            yield chunk

    @classmethod
    async def ainvoke_lc_messages(
        cls,
        lc_messages: list,
        *,
        organization_id: Optional[str] = None,
        purpose: AIModelPurpose = AIModelPurpose.CHAT_COMPLETION,
        selected_model_config_id: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        runtime = await AIModelConfigService.resolve_model_config(
            purpose=purpose,
            selected_model_config_id=selected_model_config_id,
            organization_id=organization_id,
            custom_api_key=custom_api_key,
        )
        max_tokens = max_tokens or settings.GEMINI_MAX_TOKENS
        temperature = temperature if temperature is not None else settings.GEMINI_TEMPERATURE

        try:
            if runtime.provider == AIModelProvider.BEEKNOEE:
                beek_resp: BeeknoeeResponse = await beeknoee_ainvoke(
                    lc_messages,
                    api_key=runtime.api_key,
                    model=runtime.model_name,
                    base_url=runtime.api_base_url or settings.beeknoee_api_base,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                return LLMResponse(
                    content=beek_resp.content,
                    tokens_used=beek_resp.total_tokens,
                    model=runtime.model_name,
                )

            model = cls._get_gemini_model(
                runtime,
                streaming=False,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            response = await model.ainvoke(lc_messages)
            tokens_used = response.usage_metadata.get("total_tokens", 0) if response.usage_metadata else 0
            return LLMResponse(
                content=str(response.content),
                tokens_used=tokens_used,
                model=runtime.model_name,
            )
        except Exception as exc:
            raise LLMException(f"LLM invocation failed: {exc}") from exc

    @classmethod
    async def astream_lc_messages(
        cls,
        lc_messages: list,
        *,
        organization_id: Optional[str] = None,
        purpose: AIModelPurpose = AIModelPurpose.CHAT_COMPLETION,
        selected_model_config_id: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        on_complete: Optional[Callable[[LLMResponse], None]] = None,
    ) -> AsyncGenerator[str, None]:
        runtime = await AIModelConfigService.resolve_model_config(
            purpose=purpose,
            selected_model_config_id=selected_model_config_id,
            organization_id=organization_id,
            custom_api_key=custom_api_key,
        )
        max_tokens = max_tokens or settings.GEMINI_MAX_TOKENS
        temperature = temperature if temperature is not None else settings.GEMINI_TEMPERATURE

        try:
            if runtime.provider == AIModelProvider.BEEKNOEE:
                usage_prompt_tokens = 0
                usage_completion_tokens = 0
                usage_total_tokens = 0

                def _on_beek_usage(prompt_tokens: int, completion_tokens: int, total_tokens: int) -> None:
                    nonlocal usage_prompt_tokens, usage_completion_tokens, usage_total_tokens
                    usage_prompt_tokens = max(usage_prompt_tokens, prompt_tokens)
                    usage_completion_tokens = max(usage_completion_tokens, completion_tokens)
                    usage_total_tokens = max(usage_total_tokens, total_tokens)

                async for chunk in beeknoee_astream(
                    lc_messages,
                    api_key=runtime.api_key,
                    model=runtime.model_name,
                    base_url=runtime.api_base_url or settings.beeknoee_api_base,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    on_usage=_on_beek_usage,
                ):
                    yield chunk

                if on_complete:
                    on_complete(
                        LLMResponse(
                            content="",
                            tokens_used=usage_total_tokens,
                            model=runtime.model_name,
                        )
                    )
                return

            model = cls._get_gemini_model(
                runtime,
                streaming=True,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            streamed_text: list[str] = []
            usage_total_tokens = 0
            async for chunk in model.astream(lc_messages):
                if getattr(chunk, "usage_metadata", None):
                    usage_total_tokens = max(
                        usage_total_tokens,
                        int(chunk.usage_metadata.get("total_tokens", 0) or 0),
                    )
                if chunk.content:
                    text = str(chunk.content)
                    streamed_text.append(text)
                    yield text

            if on_complete:
                estimated_tokens = max(1, len("".join(streamed_text)) // 4) if streamed_text else 0
                on_complete(
                    LLMResponse(
                        content="",
                        tokens_used=usage_total_tokens or estimated_tokens,
                        model=runtime.model_name,
                    )
                )
        except Exception as exc:
            raise LLMException(f"LLM streaming failed: {exc}") from exc

    @classmethod
    async def ainvoke_with_tools(
        cls,
        messages: list[ChatMessage],
        tools: list[dict[str, Any]],
        *,
        organization_id: Optional[str] = None,
        purpose: AIModelPurpose = AIModelPurpose.TOOL_PLANNING,
        selected_model_config_id: Optional[str] = None,
        custom_api_key: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> LLMResponse:
        """
        Invoke LLM with function calling support (bind_tools).
        Returns content + tool_calls extracted from the response.
        """
        lc_messages = cls._build_messages(messages)
        runtime = await AIModelConfigService.resolve_model_config(
            purpose=purpose,
            selected_model_config_id=selected_model_config_id,
            organization_id=organization_id,
            custom_api_key=custom_api_key,
        )
        max_tokens = max_tokens or settings.GEMINI_MAX_TOKENS
        temperature = temperature if temperature is not None else settings.GEMINI_TEMPERATURE

        try:
            if runtime.provider == AIModelProvider.BEEKNOEE:
                # Beeknoee does not support native tool calling yet — fallback to Gemini
                log = __import__("structlog").get_logger(__name__)
                log.warning(
                    "llm_service.fallback_to_gemini_for_tools",
                    beeknoee_model=runtime.model_name,
                    purpose=purpose.value,
                )
                # Build a fallback Gemini runtime using env defaults
                fallback_runtime = ResolvedAIModelConfig(
                    model_name=settings.GEMINI_CHAT_MODEL,
                    api_key=settings.GEMINI_API_KEY,
                    provider=AIModelProvider.GEMINI,
                    model_display_name="Gemini (fallback for tools)",
                )
                model = cls._get_gemini_model(
                    fallback_runtime,
                    streaming=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            else:
                model = cls._get_gemini_model(
                    runtime,
                    streaming=False,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

            model_with_tools = model.bind_tools(tools)
            response = await model_with_tools.ainvoke(lc_messages)

            tokens_used = response.usage_metadata.get("total_tokens", 0) if response.usage_metadata else 0

            # Extract tool_calls from response
            raw_tool_calls = getattr(response, "tool_calls", [])
            tool_calls: list[dict[str, Any]] = []
            for tc in raw_tool_calls or []:
                tool_calls.append({
                    "name": tc.get("name"),
                    "args": tc.get("args", {}),
                })

            return LLMResponse(
                content=str(response.content),
                tokens_used=tokens_used,
                model=runtime.model_name,
                tool_calls=tool_calls,
            )
        except Exception as exc:
            raise LLMException(f"LLM tool invocation failed: {exc}") from exc

    @classmethod
    async def ainvoke_with_key(
        cls,
        lc_messages: list,
        custom_api_key: Optional[str] = None,
    ) -> LLMResponse:
        return await cls.ainvoke_lc_messages(
            lc_messages,
            custom_api_key=custom_api_key,
        )

    @classmethod
    async def astream_with_key(
        cls,
        lc_messages: list,
        custom_api_key: Optional[str] = None,
        on_complete: Optional[Callable[[LLMResponse], None]] = None,
    ) -> AsyncGenerator[str, None]:
        async for chunk in cls.astream_lc_messages(
            lc_messages,
            custom_api_key=custom_api_key,
            on_complete=on_complete,
        ):
            yield chunk

