from __future__ import annotations

from app.agents.retrieval_agent import RetrievedChunk


class OrchestratorContextBuilder:
    """Pure helpers for chat-mode selection and web context formatting."""

    @staticmethod
    def determine_chat_mode(
        is_chitchat: bool,
        mode: str,
        chunks: list[RetrievedChunk],
        web_sources: list[dict],
    ) -> str:
        """Map orchestrator mode to ChatAgent internal mode."""
        if mode == "general":
            return "direct"
        if mode == "web":
            return "web"
        if mode in {"auto", "hybrid"}:
            if is_chitchat and not chunks and not web_sources:
                return "direct"
            if chunks and web_sources:
                return "hybrid"
            if web_sources:
                return "web"
            if chunks:
                return "rag"
            return "hybrid"
        # mode == "rag"
        if is_chitchat:
            return "direct"
        return "rag"

    @staticmethod
    def build_web_context(web_sources: list[dict]) -> str:
        """Format web sources into one prompt-ready context string."""
        parts = []
        for i, src in enumerate(web_sources, start=1):
            title = src.get("title", "")
            url = src.get("url", "")
            snippet = src.get("snippet", "")[:500]
            parts.append(f"[{i}] {title}\\nURL: {url}\\n{snippet}")
        return "\\n\\n---\\n\\n".join(parts)
