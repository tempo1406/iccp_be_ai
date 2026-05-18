from __future__ import annotations

import re
import unicodedata

_VIETNAMESE_CHAR_RE = re.compile(
    r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệ"
    r"íìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
)

_VI_HINT_WORDS = {
    "ban",
    "bao",
    "biet",
    "cach",
    "can",
    "cho",
    "chinh",
    "co",
    "cong",
    "cua",
    "duoc",
    "gi",
    "huong",
    "la",
    "lam",
    "lieu",
    "nghi",
    "nhan",
    "noi",
    "quy",
    "sach",
    "tai",
    "the",
    "thong",
    "tin",
    "toi",
    "trinh",
    "tro",
    "tu",
    "van",
    "ve",
    "viet",
}

_EN_HINT_WORDS = {
    "a",
    "about",
    "an",
    "and",
    "document",
    "employee",
    "english",
    "explain",
    "guide",
    "help",
    "how",
    "internal",
    "is",
    "leave",
    "me",
    "on",
    "policy",
    "process",
    "procedure",
    "show",
    "tell",
    "the",
    "this",
    "what",
    "where",
}


def _ascii_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", (text or "").lower())
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z0-9']+", ascii_text)


class LanguageService:
    @classmethod
    def detect_language(cls, text: str) -> str:
        normalized = (text or "").strip().lower()
        if not normalized:
            return "unknown"

        if _VIETNAMESE_CHAR_RE.search(normalized):
            return "vi"

        tokens = _ascii_tokens(normalized)
        if not tokens:
            return "unknown"

        vi_score = sum(1 for token in tokens if token in _VI_HINT_WORDS)
        en_score = sum(1 for token in tokens if token in _EN_HINT_WORDS)

        if vi_score > en_score:
            return "vi"
        if en_score > vi_score:
            return "en"

        # Default ASCII-heavy queries to English unless we have clear Vietnamese hints.
        return "en"

    @classmethod
    def response_language_name(cls, language: str) -> str:
        if language == "en":
            return "tiếng Anh"
        return "tiếng Việt"

    @classmethod
    def response_language_instruction_from_text(cls, text: str) -> str:
        language = cls.detect_language(text)
        language_name = cls.response_language_name(language)
        return (
            f"Ưu tiên trả lời bằng {language_name}. "
            "Nếu người dùng đổi ngôn ngữ ở tin nhắn mới hơn, hãy theo ngôn ngữ mới nhất của họ."
        )
