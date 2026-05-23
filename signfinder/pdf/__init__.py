"""PDF/DOCX обработка: парсинг, наложение подписи, рендер превью, детекция языка."""
from signfinder.pdf.language import detect_language
from signfinder.pdf.overlay import apply_signature
from signfinder.pdf.parser import (
    ParsedDocument,
    ParsedPage,
    Word,
    docx_to_pdf,
    parse_document,
    parse_pdf_bytes,
)
from signfinder.pdf.preview import render_page_with_highlights

__all__ = [
    "ParsedDocument",
    "ParsedPage",
    "Word",
    "parse_document",
    "parse_pdf_bytes",
    "docx_to_pdf",
    "apply_signature",
    "render_page_with_highlights",
    "detect_language",
]
