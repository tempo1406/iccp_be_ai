"""
Content Policy Service
======================
Two-layer defense for both document ingestion and user chat input:

Layer 1 — LLM-based moderation
  Detects: hate, harassment, violence, sexual content, self-harm, etc.

Layer 2 — Prompt Injection Detector
  Detects patterns that attempt to hijack LLM instructions.
  Applies to:
    - Document content before chunking  (indirect injection)
    - User chat messages                (direct injection)
    - Retrieved chunk content           (indirect via retrieval)

Layer 3 — Custom Business Rules (extensible)
  Detects: PII patterns, confidential markers, restricted keywords.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

import structlog
import json
from langchain_core.messages import HumanMessage

from app.core.exceptions import ContentPolicyViolationException, PromptInjectionException
from app.schemas.ai_model_config import AIModelPurpose
from app.services.llm_service import LLMService

log = structlog.get_logger(__name__)

# ── Prompt injection regex patterns ───────────────────────────────────────────
# Ordered from most specific to most general.
_INJECTION_PATTERNS: list[tuple[str, str]] = [
    # Classic instruction override
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?|prompts?|context)", "instruction_override"),
    (r"disregard\s+(all\s+)?(previous|prior|above|earlier)\s+(instructions?|rules?)", "instruction_override"),
    (r"forget\s+(all\s+)?(previous|prior|your)\s+(instructions?|rules?|context|training)", "instruction_override"),

    # Role/persona hijacking
    (r"you\s+are\s+now\s+(a|an|the)\s+\w+", "persona_hijack"),
    (r"act\s+as\s+(a|an|the)?\s*(unrestricted|uncensored|jailbreak|DAN|evil)", "persona_hijack"),
    (r"(pretend|imagine|roleplay)\s+(you('re| are)|that you('re| are))\s+(a|an|the)?\s*(different|new|unrestricted)", "persona_hijack"),
    (r"\bDAN\b.*\bjailbreak\b", "jailbreak"),
    (r"jailbreak\s+(mode|prompt|yourself)", "jailbreak"),

    # System prompt leakage attempts
    (r"(print|repeat|output|show|reveal|display|tell me)\s+(your\s+)?(system\s+prompt|instructions?|initial\s+prompt)", "prompt_leakage"),
    (r"what\s+(are|were)\s+your\s+(original\s+)?(instructions?|rules?|system\s+prompt)", "prompt_leakage"),

    # Delimiter injection (XML/markdown attacks)
    (r"</?(system|instructions?|context|prompt|human|assistant)\s*>", "delimiter_injection"),
    (r"\[/?INST\]", "delimiter_injection"),
    (r"<<SYS>>|<</SYS>>", "delimiter_injection"),

    # Context window poisoning via embedded instructions
    (r"(SYSTEM|INSTRUCTION|OVERRIDE)\s*:\s*(ignore|forget|disregard)", "context_poisoning"),
    (r"---+\s*(NEW\s+)?(SYSTEM\s+)?(PROMPT|INSTRUCTION)", "context_poisoning"),

    # Data exfiltration attempts
    (r"(send|forward|email|post|upload)\s+(all|this|the)\s+(data|information|documents?|context)", "exfiltration"),
    (r"(leak|expose|dump)\s+(the\s+)?(database|documents?|system|internal)", "exfiltration"),
]

_COMPILED_PATTERNS = [
    (re.compile(pattern, re.IGNORECASE | re.MULTILINE), label)
    for pattern, label in _INJECTION_PATTERNS
]

# ── PII / sensitive data patterns ─────────────────────────────────────────────
_SENSITIVE_PATTERNS: list[tuple[str, str]] = [
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn"),                                  # US SSN
    (r"\b4[0-9]{12}(?:[0-9]{3})?\b", "credit_card_visa"),               # Visa
    (r"\b(?:5[1-5][0-9]{2}|222[1-9]|22[3-9][0-9]|2[3-6][0-9]{2}|27[01][0-9]|2720)[0-9]{12}\b", "credit_card_mc"),
    (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b", "email"),  # general — may be intentional
    (r"password\s*[:=]\s*\S+", "plaintext_password"),
    (r"secret\s*[:=]\s*\S+", "plaintext_secret"),
    (r"api[_\s]?key\s*[:=]\s*\S+", "api_key_exposure"),
    (r"private[_\s]?key\s*[:=]\s*\S+", "private_key_exposure"),
]

_COMPILED_SENSITIVE = [
    (re.compile(pattern, re.IGNORECASE), label)
    for pattern, label in _SENSITIVE_PATTERNS
]

# Moderation categories that block ingestion
_HARD_BLOCK_CATEGORIES = {
    "hate", "hate/threatening",
    "harassment", "harassment/threatening",
    "violence", "violence/graphic",
    "self-harm", "self-harm/instructions",
    "sexual/minors",
}

# Moderation categories that are flagged but allowed (with warning log)
_SOFT_FLAG_CATEGORIES = {
    "sexual", "self-harm/intent",
}

# Score threshold: if any hard block category score > this, block it
_MODERATION_THRESHOLD = 0.7


@dataclass
class PolicyCheckResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    injection_patterns_found: list[str] = field(default_factory=list)
    sensitive_data_found: list[str] = field(default_factory=list)
    moderation_flagged: bool = False
    moderation_categories: list[str] = field(default_factory=list)


class ContentPolicyService:
    """
    Stateless service — all methods are classmethods.
    Call check_document() before chunking during ingestion.
    Call check_user_input() before routing during chat.
    """

    # ── Public API ─────────────────────────────────────────────────────────

    @classmethod
    async def check_document(
        cls,
        text: str,
        document_id: str,
        organization_id: str,
        file_name: str = "",
    ) -> PolicyCheckResult:
        """
        Full policy check for document content before chunking.
        Raises ContentPolicyViolationException if hard violations found.
        Returns PolicyCheckResult with warnings for soft violations.
        """
        result = PolicyCheckResult(passed=True)

        # 1. Injection pattern scan (critical — stop ingestion if found)
        injection_result = cls._scan_injection(text)
        if injection_result:
            result.injection_patterns_found = injection_result
            result.passed = False
            result.violations.append(f"Prompt injection patterns detected: {injection_result}")
            log.warning(
                "content_policy.document_injection_detected",
                document_id=document_id,
                organization_id=organization_id,
                file_name=file_name,
                patterns=injection_result,
            )
            raise PromptInjectionException(
                message=f"Document '{file_name}' contains prompt injection patterns: {injection_result}. Ingestion blocked.",
                source="document",
            )

        # 2. Moderation model check (sample first 8000 chars to limit API cost)
        sample = text[:8000]
        moderation_result = await cls._run_moderation(sample, organization_id=organization_id)
        if moderation_result["hard_blocked"]:
            result.passed = False
            result.moderation_flagged = True
            result.moderation_categories = moderation_result["categories"]
            result.violations.append(f"Content moderation violation: {moderation_result['categories']}")
            log.warning(
                "content_policy.document_moderation_blocked",
                document_id=document_id,
                organization_id=organization_id,
                file_name=file_name,
                categories=moderation_result["categories"],
            )
            raise ContentPolicyViolationException(
                message=f"Document '{file_name}' violates content policy: {moderation_result['categories']}",
                violation_type="moderation",
                categories=moderation_result["categories"],
            )

        if moderation_result["soft_flagged"]:
            result.moderation_flagged = True
            result.moderation_categories = moderation_result["categories"]
            result.warnings.append(f"Content moderation soft flag: {moderation_result['categories']}")
            log.info(
                "content_policy.document_soft_flag",
                document_id=document_id,
                file_name=file_name,
                categories=moderation_result["categories"],
            )

        # 3. Sensitive data scan (warning only — PII may be legitimate in internal docs)
        sensitive = cls._scan_sensitive_data(text)
        if sensitive:
            result.sensitive_data_found = sensitive
            result.warnings.append(f"Sensitive data patterns found: {sensitive}")
            log.info(
                "content_policy.document_sensitive_data",
                document_id=document_id,
                file_name=file_name,
                patterns=sensitive,
            )

        log.info(
            "content_policy.document_passed",
            document_id=document_id,
            organization_id=organization_id,
            file_name=file_name,
            warnings=len(result.warnings),
        )
        return result

    @classmethod
    async def check_user_input(
        cls,
        text: str,
        user_id: str,
        organization_id: str,
    ) -> PolicyCheckResult:
        """
        Policy check for user chat messages.
        Raises PromptInjectionException if injection detected.
        Raises ContentPolicyViolationException for hard moderation hits.
        """
        result = PolicyCheckResult(passed=True)

        # 1. Injection detection (fast — regex, no API call)
        injection_result = cls._scan_injection(text)
        if injection_result:
            result.injection_patterns_found = injection_result
            result.passed = False
            log.warning(
                "content_policy.user_injection_detected",
                user_id=user_id,
                organization_id=organization_id,
                patterns=injection_result,
                message_preview=text[:100],
            )
            raise PromptInjectionException(
                message="Tin nhắn chứa nội dung không hợp lệ. Vui lòng đặt câu hỏi theo cách khác.",
                source="user_input",
            )

        # 2. Moderation check
        moderation_result = await cls._run_moderation(text, organization_id=organization_id)
        if moderation_result["hard_blocked"]:
            result.passed = False
            result.moderation_flagged = True
            result.moderation_categories = moderation_result["categories"]
            log.warning(
                "content_policy.user_moderation_blocked",
                user_id=user_id,
                organization_id=organization_id,
                categories=moderation_result["categories"],
            )
            raise ContentPolicyViolationException(
                message="Tin nhắn vi phạm chính sách nội dung. Vui lòng thử lại với nội dung phù hợp hơn.",
                violation_type="moderation",
                categories=moderation_result["categories"],
            )

        return result

    @classmethod
    def sanitize_for_context(cls, text: str) -> str:
        """
        Sanitize a chunk of document text before embedding it into the LLM prompt context.
        Strips XML-like tags, instruction delimiters that could hijack the model.
        Does NOT modify the text stored in Pinecone — only the copy used in the prompt.
        """
        # Remove XML-like tag injection
        text = re.sub(r"</?(?:system|instructions?|prompt|human|assistant|context)\s*>", "", text, flags=re.IGNORECASE)
        # Remove common delimiter attacks
        text = re.sub(r"\[/?INST\]|<<SYS>>|<</SYS>>", "", text)
        # Remove excessive dashes/equals that could simulate section separators
        text = re.sub(r"[-=]{10,}", "─" * 10, text)
        return text.strip()

    @classmethod
    def sanitize_user_message(cls, text: str) -> str:
        """
        Light sanitization of user message for display safety.
        Does NOT remove meaningful content — just neutralizes delimiter chars.
        """
        text = re.sub(r"</?(?:system|instructions?)\s*>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\[/?INST\]|<<SYS>>|<</SYS>>", "", text)
        return text.strip()

    # ── Private helpers ────────────────────────────────────────────────────

    @classmethod
    def _scan_injection(cls, text: str) -> list[str]:
        """Return list of matched injection pattern labels. Empty = clean."""
        found: list[str] = []
        for pattern, label in _COMPILED_PATTERNS:
            if pattern.search(text):
                if label not in found:
                    found.append(label)
        return found

    @classmethod
    def _scan_sensitive_data(cls, text: str) -> list[str]:
        """Return list of matched sensitive data pattern labels."""
        found: list[str] = []
        for pattern, label in _COMPILED_SENSITIVE:
            if pattern.search(text):
                if label not in found:
                    found.append(label)
        return found

    @classmethod
    async def _run_moderation(
        cls,
        text: str,
        *,
        organization_id: Optional[str] = None,
    ) -> dict:
        """Call the configured moderation model and return block/flag info."""
        try:
            prompt = f"""
You are a strict content moderation API. Analyze the following text and determine if it belongs to any of these categories:
- hate
- harassment
- violence
- self-harm
- sexual
- sexual/minors

Return ONLY a valid JSON object with a single key "categories" containing a list of strings of the identified categories. If no violations are found, return {{"categories": []}}. Do not include markdown formatting or any other text.

Text to analyze:
{text}
"""
            response = await LLMService.ainvoke_lc_messages(
                [HumanMessage(content=prompt)],
                organization_id=organization_id,
                purpose=AIModelPurpose.CONTENT_MODERATION,
                max_tokens=100,
                temperature=0.0,
            )
            content = response.content.strip()
            
            categories = []
            match = re.search(r'\{.*\}', content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group(0))
                    categories = data.get("categories", [])
                except Exception:
                    pass

            flagged_hard: list[str] = []
            flagged_soft: list[str] = []

            if isinstance(categories, list):
                for cat in categories:
                    normalized_cat = cat.replace("_", "/").replace("-", "/")
                    if normalized_cat in _HARD_BLOCK_CATEGORIES or cat in _HARD_BLOCK_CATEGORIES:
                        flagged_hard.append(cat)
                    elif normalized_cat in _SOFT_FLAG_CATEGORIES or cat in _SOFT_FLAG_CATEGORIES:
                        flagged_soft.append(cat)

            return {
                "hard_blocked": bool(flagged_hard),
                "soft_flagged": bool(flagged_soft),
                "categories": flagged_hard + flagged_soft,
            }

        except ContentPolicyViolationException:
            raise
        except Exception as exc:
            # Moderation API failure → log and allow (fail open to not block legit docs)
            log.error("content_policy.moderation_api_failed", error=str(exc))
            return {"hard_blocked": False, "soft_flagged": False, "categories": []}
