from typing import Any


class ICCPAIBaseException(Exception):
    """Base exception for all ICCP AI service errors."""

    def __init__(self, message: str, detail: Any = None) -> None:
        self.message = message
        self.detail = detail
        super().__init__(message)


class VectorStoreException(ICCPAIBaseException):
    """Raised when Pinecone operations fail."""
    pass


class EmbeddingException(ICCPAIBaseException):
    """Raised when embedding generation fails."""
    pass


class LLMException(ICCPAIBaseException):
    """Raised when LLM API calls fail."""
    pass


class DocumentParsingException(ICCPAIBaseException):
    """Raised when file parsing fails."""
    pass


class ChunkingException(ICCPAIBaseException):
    """Raised when text chunking fails."""
    pass


class IngestionException(ICCPAIBaseException):
    """Raised when document ingestion pipeline fails."""
    pass


class BeCoreClientException(ICCPAIBaseException):
    """Raised when internal API call to iccp_be_core fails."""

    def __init__(self, message: str, status_code: int = 0, detail: Any = None) -> None:
        self.status_code = status_code
        super().__init__(message, detail)


class TenantIsolationException(ICCPAIBaseException):
    """Raised when tenant context is missing or invalid — security violation."""
    pass


class UnauthorizedException(ICCPAIBaseException):
    """Raised when JWT is missing or invalid."""
    pass


class AgentException(ICCPAIBaseException):
    """Raised when an agent encounters an unrecoverable error."""
    pass


class ConversationNotFoundException(ICCPAIBaseException):
    """Raised when a conversation is not found."""
    pass


class ContentPolicyViolationException(ICCPAIBaseException):
    """
    Raised when document content or user message violates content policy.
    Includes the violation category and severity.
    """

    def __init__(
        self,
        message: str,
        violation_type: str = "unknown",
        categories: list[str] | None = None,
        detail: object = None,
    ) -> None:
        self.violation_type = violation_type  # moderation | injection | custom_rule
        self.categories = categories or []
        super().__init__(message, detail)


class PromptInjectionException(ICCPAIBaseException):
    """Raised when a prompt injection attempt is detected in user input or document."""

    def __init__(self, message: str, source: str = "user_input") -> None:
        self.source = source  # user_input | document | history
        super().__init__(message)


class QuotaExceededException(ICCPAIBaseException):
    """Raised when a user or organization has exceeded their quota."""

    def __init__(self, message: str, quota_type: str = "unknown") -> None:
        self.quota_type = quota_type  # user_daily | org_monthly | ingestion
        super().__init__(message)
