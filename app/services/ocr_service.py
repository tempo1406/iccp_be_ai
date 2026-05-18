from __future__ import annotations

import asyncio
from io import BytesIO

import structlog

from app.core.exceptions import DocumentParsingException

log = structlog.get_logger(__name__)


class OcrService:
    """Shared OCR service for documents and future chatbot image OCR."""

    @classmethod
    async def ocr_pdf_file(cls, file_path: str) -> str:
        """OCR toàn bộ PDF bằng Tesseract."""
        return await asyncio.to_thread(cls._ocr_pdf_file_sync, file_path)

    @staticmethod
    def _ocr_pdf_file_sync(file_path: str) -> str:
        try:
            import pytesseract
            from pdf2image import convert_from_path
        except ImportError as exc:
            raise DocumentParsingException(
                f"OCR dependencies chưa được cài: {exc}. "
                "Cần: pytesseract, pdf2image, tesseract-ocr"
            ) from exc

        images = convert_from_path(file_path, dpi=200)
        text_parts: list[str] = []
        for i, img in enumerate(images, start=1):
            ocr_text = pytesseract.image_to_string(
                img,
                lang="vie+eng",
                config="--psm 3",
            ).strip()
            if ocr_text:
                text_parts.append(f"[Page {i}]\\n{ocr_text}")

        return "\\n\\n".join(text_parts)

    @classmethod
    async def ocr_pdf_pages(cls, file_path: str, page_numbers: list[int]) -> list[str]:
        """OCR một số trang cụ thể trong PDF."""
        return await asyncio.to_thread(cls._ocr_pdf_pages_sync, file_path, page_numbers)

    @staticmethod
    def _ocr_pdf_pages_sync(file_path: str, page_numbers: list[int]) -> list[str]:
        try:
            import pytesseract
            from pdf2image import convert_from_path
        except ImportError as exc:
            raise DocumentParsingException(
                f"OCR dependencies chưa được cài: {exc}. "
                "Cần: pytesseract, pdf2image, tesseract-ocr"
            ) from exc

        results: list[str] = []
        for page_num in page_numbers:
            images = convert_from_path(
                file_path,
                dpi=200,
                first_page=page_num,
                last_page=page_num,
            )
            if not images:
                results.append("")
                continue

            text = pytesseract.image_to_string(
                images[0],
                lang="vie+eng",
                config="--psm 3",
            ).strip()
            results.append(text)

        return results

    @classmethod
    async def ocr_image_bytes(cls, image_bytes: bytes) -> str:
        """OCR từ bytes ảnh (dùng cho ảnh embedded và chatbot image input)."""
        return await asyncio.to_thread(cls._ocr_image_bytes_sync, image_bytes)

    @staticmethod
    def _ocr_image_bytes_sync(image_bytes: bytes) -> str:
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise DocumentParsingException(
                f"OCR dependencies chưa được cài: {exc}. "
                "Cần: pytesseract, pillow, tesseract-ocr"
            ) from exc

        try:
            with Image.open(BytesIO(image_bytes)) as img:
                text = pytesseract.image_to_string(
                    img,
                    lang="vie+eng",
                    config="--psm 3",
                )
            return text.strip()
        except Exception as exc:
            log.warning("ocr.image_failed", error=str(exc))
            return ""
