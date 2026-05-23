"""Вычисление fingerprint документа для matching шаблонов (v1.8+)."""
from __future__ import annotations

import re
import sys

from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

_PREAMBLE_MARKERS = {
    "ru": ["именуем", "в лице", "настоящ", "заключил", "с одной стороны"],
    "en": ["hereinafter", "represented by", "between", "agreed as follows"],
    "pl": ["umow", "zawart", "reprezentowan", "zwan"],
}

_SECTION_RE = re.compile(
    r"(\d+\.\s+[А-ЯA-Z]"
    r"|РАЗДЕЛ|ПРИЛОЖЕНИЕ|АКТ|СТАТЬЯ"
    r"|Section|Annex|Appendix|Schedule|Article"
    r"|Część|Załącznik|Rozdział|Artykuł)",
    re.MULTILINE | re.UNICODE,
)

_MIN_CHARS_HEADER = 300


def compute_fingerprint(doc, language: str) -> dict:
    """
    Возвращает fingerprint документа:
    {
        "page_count", "total_chars", "chars_per_page",
        "header_simhash", "section_titles", "language"
    }

    doc: fitz.Document (открытый PyMuPDF документ, не ParsedDocument)
    """
    try:
        chars_per_page = []
        for i in range(len(doc)):
            text = doc[i].get_text("text")
            chars_per_page.append(len(text))

        total_chars = sum(chars_per_page)
        header_idx = find_header_page(doc, language)
        header_simhash = compute_header_simhash(doc, header_idx)
        section_titles = extract_section_titles(doc)

        return {
            "page_count": len(doc),
            "total_chars": total_chars,
            "chars_per_page": chars_per_page,
            "header_simhash": header_simhash,
            "section_titles": section_titles,
            "language": language,
        }
    except Exception as e:
        logger.error("compute_fingerprint failed: %s", e)
        sys.stderr.write(f"[fingerprint] compute_fingerprint: {e}\n")
        return {
            "page_count": len(doc),
            "total_chars": 0,
            "chars_per_page": [],
            "header_simhash": "",
            "section_titles": [],
            "language": language,
        }


def find_header_page(doc, language: str) -> int:
    """Индекс страницы с преамбулой. Fallback → 0."""
    markers = _PREAMBLE_MARKERS.get(language[:2].lower(), _PREAMBLE_MARKERS["ru"])
    scan_pages = min(3, len(doc))

    for i in range(scan_pages):
        text = doc[i].get_text("text")
        sig_chars = len(re.sub(r"\s+", "", text))
        if sig_chars < _MIN_CHARS_HEADER:
            continue
        text_lower = text.lower()
        if any(m in text_lower for m in markers):
            return i

    for i in range(scan_pages):
        text = doc[i].get_text("text")
        if len(re.sub(r"\s+", "", text)) >= _MIN_CHARS_HEADER:
            return i

    return 0


def compute_header_simhash(doc, page_idx: int) -> str:
    """SimHash текста шапки. Пустая строка при ошибке."""
    try:
        from simhash import Simhash
        text = doc[page_idx].get_text("text")
        return str(Simhash(text).value)
    except ImportError:
        sys.stderr.write("[fingerprint] simhash not installed, skipping header_simhash\n")
        return ""
    except Exception as e:
        logger.error("compute_header_simhash failed: %s", e)
        sys.stderr.write(f"[fingerprint] compute_header_simhash: {e}\n")
        return ""


def extract_section_titles(doc) -> list[str]:
    """Заголовки разделов из всего документа через regex.

    Использует get_text("dict") для итерации по реальным PDF-строкам
    (block → line → spans) — не зависит от splitlines() и кодировок
    при обратном чтении из insert_text с базовым шрифтом.
    """
    titles = []
    seen = set()
    try:
        for i in range(len(doc)):
            page_dict = doc[i].get_text("dict")
            for block in page_dict.get("blocks", []):
                if block.get("type") != 0:  # 0 = text, пропускаем изображения
                    continue
                for line in block.get("lines", []):
                    line_text = "".join(
                        span.get("text", "") for span in line.get("spans", [])
                    ).strip()
                    if not line_text or len(line_text) > 200:
                        continue
                    if _SECTION_RE.search(line_text) and line_text not in seen:
                        seen.add(line_text)
                        titles.append(line_text)
    except Exception as e:
        logger.error("extract_section_titles failed: %s", e)
        sys.stderr.write(f"[fingerprint] extract_section_titles: {e}\n")
    return titles
