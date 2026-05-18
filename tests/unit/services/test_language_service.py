from app.services.language_service import LanguageService


def test_detect_language_returns_vietnamese_for_diacritics():
    assert LanguageService.detect_language("Chính sách nghỉ phép của công ty") == "vi"


def test_detect_language_returns_english_for_ascii_query():
    assert LanguageService.detect_language("What is the employee leave policy?") == "en"
