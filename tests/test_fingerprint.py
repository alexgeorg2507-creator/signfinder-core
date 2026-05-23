"""Smoke-тесты fingerprint на сгенерированном PDF."""
from __future__ import annotations

import io

import fitz
import pytest

from signfinder.fingerprint import (
    compute_fingerprint,
    extract_section_titles,
    find_header_page,
)


@pytest.fixture
def sample_pdf_doc():
    """Простой 2-страничный PDF.

    Используем Latin-текст: дефолтный шрифт Helvetica в PyMuPDF
    не содержит кириллических глифов — они заменяются на '·'.
    Regex-паттерны проверяются на латинице (Section, Annex).
    """
    doc = fitz.open()

    page1 = doc.new_page()
    page1.insert_text((50, 50), "LEASE AGREEMENT No 123/2026", fontsize=12)
    page1.insert_text(
        (50, 80),
        "Romashka LLC, hereinafter referred to as the Lessor, "
        "represented by Director Ivanov I.I., on the one hand, and "
        "Lutik LLC, hereinafter referred to as the Lessee, "
        "represented by Director Petrov P.P., on the other hand, "
        "have agreed as follows:",
        fontsize=10,
    )
    page1.insert_text((50, 160), "1. Subject Of The Agreement", fontsize=10)
    page1.insert_text(
        (50, 180),
        "1.1. The Lessor transfers to the Lessee for temporary use...",
        fontsize=10,
    )

    page2 = doc.new_page()
    page2.insert_text((50, 50), "Section 2. Obligations", fontsize=10)
    page2.insert_text((50, 70), "Annex No 1", fontsize=10)
    page2.insert_text((50, 90), "Signatures:", fontsize=10)
    page2.insert_text((50, 110), "Lessor: _________________ /Ivanov I.I./", fontsize=10)
    page2.insert_text((50, 130), "Lessee: _________________ /Petrov P.P./", fontsize=10)

    buf = io.BytesIO()
    doc.save(buf)
    pdf_bytes = buf.getvalue()
    doc.close()

    return fitz.open(stream=pdf_bytes, filetype="pdf")


def test_compute_fingerprint_basic(sample_pdf_doc):
    fp = compute_fingerprint(sample_pdf_doc, language="en")
    assert fp["page_count"] == 2
    assert fp["total_chars"] > 100
    assert len(fp["chars_per_page"]) == 2
    assert fp["language"] == "en"
    assert isinstance(fp["section_titles"], list)


def test_find_header_page_finds_preamble(sample_pdf_doc):
    """Preamble with 'hereinafter', 'represented by' → page 0."""
    idx = find_header_page(sample_pdf_doc, "en")
    assert idx == 0


def test_find_header_page_fallback(sample_pdf_doc):
    """Неподдерживаемый язык → fallback на первую содержательную страницу."""
    idx = find_header_page(sample_pdf_doc, "xx")
    assert idx == 0


def test_extract_section_titles(sample_pdf_doc):
    titles = extract_section_titles(sample_pdf_doc)
    titles_lower = [t.lower() for t in titles]
    # '1. Subject Of The Agreement' → matches \d+\.\s+[A-Z]
    assert any("subject" in t for t in titles_lower)
    # 'Annex No 1' → matches Annex keyword
    assert any("annex" in t for t in titles_lower)


def test_header_simhash_is_string(sample_pdf_doc):
    """simhash должен быть строкой (может быть пустой если simhash не установлен)."""
    fp = compute_fingerprint(sample_pdf_doc, language="en")
    assert isinstance(fp["header_simhash"], str)


def test_fingerprint_empty_pdf():
    """Пустой PDF не должен падать."""
    doc = fitz.open()
    doc.new_page()
    fp = compute_fingerprint(doc, language="ru")
    assert fp["page_count"] == 1
    assert fp["section_titles"] == []
    doc.close()
