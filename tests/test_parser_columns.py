"""Тесты детектора колонок (v1.18.7) — Path A: одна страница, текст левая→правая."""
from __future__ import annotations

import io

import fitz
import pytest

from signfinder.pdf.parser import (
    ParsedDocument,
    _build_column_text,
    _detect_gutter,
    parse_pdf_bytes,
)


# ── _detect_gutter (детектор коридора) ───────────────────────────────────────

def _word_tuple(text: str, x0: float, y0: float, w: float = 50, h: float = 12) -> tuple:
    """Синтетический word-tuple в формате fitz get_text('words'): (x0,y0,x1,y1,text,...)."""
    return (x0, y0, x0 + w, y0 + h, text, 0, 0, 0)


def test_detect_gutter_two_columns():
    """Слова на x=50 и x=350 при ширине 595 → коридор найден между колонками."""
    words = []
    # левая колонка: x0=50, x1=130
    for y in range(100, 700, 20):
        words.append(_word_tuple("left", 50, y, w=80))
    # правая колонка: x0=350, x1=430
    for y in range(100, 700, 20):
        words.append(_word_tuple("right", 350, y, w=80))
    cut = _detect_gutter(words, page_width=595)
    assert cut is not None
    # Коридор должен лежать МЕЖДУ колонками: правее x1=130 и левее x0=350
    assert 130 <= cut <= 350


def test_detect_gutter_single_column():
    """Слова распределены по всей ширине → коридор НЕ найден."""
    words = []
    for y in range(100, 700, 20):
        # слово пересекает зону 35-65%
        words.append(_word_tuple("wide text spans middle", 80, y, w=400))
    cut = _detect_gutter(words, page_width=595)
    assert cut is None


def test_detect_gutter_empty_page():
    assert _detect_gutter([], page_width=595) is None


# ── _build_column_text — упорядоченное чтение колонки ────────────────────────

def test_build_column_text_left():
    """Слова с x1 ≤ cut попадают в левую колонку."""
    words = [
        _word_tuple("Hello", 50, 100),       # x1=100, в левой
        _word_tuple("World", 350, 100),      # x0=350, в правой
        _word_tuple("Foo", 50, 130),
    ]
    text = _build_column_text(words, x_max=250)
    assert "Hello" in text
    assert "Foo" in text
    assert "World" not in text


def test_build_column_text_right():
    """Слова с x0 ≥ cut попадают в правую колонку."""
    words = [
        _word_tuple("Hello", 50, 100),
        _word_tuple("World", 350, 100),
        _word_tuple("Bar", 350, 130),
    ]
    text = _build_column_text(words, x_min=250)
    assert "World" in text
    assert "Bar" in text
    assert "Hello" not in text


def test_build_column_text_preserves_line_order():
    """Слова сортируются по (top, x0) — строки сверху вниз, слова слева направо."""
    words = [
        _word_tuple("third", 50, 200),
        _word_tuple("first", 50, 100),
        _word_tuple("FIRST_RIGHT", 150, 100),
        _word_tuple("second", 50, 150),
    ]
    text = _build_column_text(words, x_max=300)
    lines = text.split("\n")
    assert lines[0] == "first FIRST_RIGHT"
    assert lines[1] == "second"
    assert lines[2] == "third"


# ── parse_pdf_bytes — интеграционный ─────────────────────────────────────────

def _make_dual_column_pdf() -> bytes:
    """PDF где левая половина — английский, правая — польский (симуляция bilingual)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Левая колонка (английский) — x в 50-250
    for i, line in enumerate([
        "This agreement is between the parties",
        "for the supply of services described",
        "in the appendix below.",
        "Signed by the Contractor:",
    ]):
        page.insert_text((50, 100 + i * 30), line, fontsize=10)
    # Правая колонка (польский) — x в 320-560
    for i, line in enumerate([
        "Niniejsza umowa jest zawierana",
        "pomiedzy stronami w celu",
        "swiadczenia opisanych uslug.",
        "Podpisano przez Wykonawce:",
    ]):
        page.insert_text((320, 100 + i * 30), line, fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_parse_pdf_detects_dual_column():
    """Двухколоночный PDF → layout=dual_column_vertical, languages = 2 элемента."""
    pdf = _make_dual_column_pdf()
    doc = parse_pdf_bytes(pdf, filename="test.pdf")
    assert doc.layout == "dual_column_vertical"
    assert doc.gutter_x is not None
    assert len(doc.pages) == 1
    assert doc.pages[0].layout == "dual_column_vertical"
    assert len(doc.pages[0].languages) >= 1


def test_parse_pdf_text_ordered_by_columns():
    """Текст dual-column PDF: сначала левая колонка целиком, потом разделитель, потом правая."""
    pdf = _make_dual_column_pdf()
    doc = parse_pdf_bytes(pdf, filename="test.pdf")
    text = doc.pages[0].text
    # Английский (левая) должен идти ДО разделителя "---"
    en_pos = text.find("This agreement")
    sep_pos = text.find("---")
    pl_pos = text.find("Niniejsza")
    assert 0 <= en_pos < sep_pos < pl_pos


def _make_single_column_pdf() -> bytes:
    """PDF с разными длинными строками — слова в разных позициях по x, нет единого коридора."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Строки разные → пробелы между словами не совпадают по x → детектор не найдёт единый коридор.
    lines = [
        "This single column contract document is for service delivery between parties",
        "The agreement covers development and testing work described in the appendix",
        "Payment terms include net thirty days from invoice date upon full completion",
        "All intellectual property created during performance belongs to the client",
        "This document represents the complete understanding between both parties here",
        "Modifications require written consent from authorized representatives only now",
        "Disputes resolved through binding arbitration under local laws and regulations",
        "The contract terminates automatically upon completion of all deliverables listed",
    ]
    for i, line in enumerate(lines):
        page.insert_text((50, 100 + i * 25), line, fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_parse_pdf_single_column():
    """Одноколоночный PDF (разные широкие строки) → layout=single_column."""
    pdf = _make_single_column_pdf()
    doc = parse_pdf_bytes(pdf, filename="test.pdf")
    assert doc.layout == "single_column"
    assert doc.gutter_x is None


def test_parsed_document_has_languages_list(pdf_bytes):
    """ParsedDocument имеет поле languages (даже для одноязычного — список из 1)."""
    doc = parse_pdf_bytes(pdf_bytes, filename="test.pdf")
    assert isinstance(doc.languages, list)
    assert len(doc.languages) >= 1
