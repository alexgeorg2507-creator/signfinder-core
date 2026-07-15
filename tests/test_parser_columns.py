"""Тесты детектора колонок (v1.18.7) — Path A: одна страница, текст левая→правая."""
from __future__ import annotations

import io

import fitz
import pytest

from signfinder.pdf.parser import (
    ParsedDocument,
    _build_column_text,
    _detect_gutter,
    _detect_local_dual_zones,
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


# ── _detect_local_dual_zones (Fix-10.3) — локальные двухколоночные строки ────
# Регрессия: реверс-паттерн вида `_{3,}[\s\S]{0,50}Заказчик` на однокол­оночном
# документе задваивался (совпадал и с "нашей" линией, и с линией контрагента
# в той же строке футера), потому что _detect_gutter (весь документ разом) не
# видит локальный разрыв футера на фоне слов основного текста — see CLAUDE.md
# v1.20.12. Геометрия footer-теста — реальные координаты из
# signed_1ДоговорЛебедев.pdf (стр. 0-4), снятые напрямую через PyMuPDF.

def test_detect_local_dual_zones_footer_style():
    """Строка 'Заказчик____   Подрядчик____' (в MuPDF — 4 разных 'line', но с
    вертикальным перекрытием bbox) → находит одну зону с разрывом по центру."""
    words = [
        _word_tuple("Заказчик", 35.55, 768.5, w=45.36, h=12.27),
        _word_tuple("______________________________", 80.95, 766.9, w=122.26, h=14.3),
        _word_tuple("__________________________________", 410.99, 766.9, w=138.59, h=14.3),
        _word_tuple("Подрядчик", 355.79, 768.5, w=55.59, h=12.27),
    ]
    zones = _detect_local_dual_zones(words, page_width=595)
    assert len(zones) == 1
    y0, y1, gx = zones[0]
    assert 766 <= y0 <= 769
    assert 780 <= y1 <= 782
    assert 250 <= gx <= 310  # центр разрыва между блоками


def test_detect_local_dual_zones_no_zone_on_body_text():
    """Обычные абзацы одноколоночного текста (слова через всю ширину, без
    большого разрыва по центру строки) → зон не найдено."""
    words = []
    lines = [
        "This single column contract document is for service delivery between",
        "The agreement covers development and testing work described in appendix",
        "Payment terms include net thirty days from invoice date upon completion",
    ]
    for i, line in enumerate(lines):
        x = 50
        for word in line.split():
            w = len(word) * 6
            words.append(_word_tuple(word, x, 100 + i * 20, w=w, h=12))
            x += w + 5
    zones = _detect_local_dual_zones(words, page_width=595)
    assert zones == []


def test_detect_local_dual_zones_empty():
    assert _detect_local_dual_zones([], page_width=595) == []


def test_detect_local_dual_zones_ignores_edge_gap():
    """Разрыв, центр которого лежит у самого края страницы (вне 25-75%), не
    считается зоной — это короткая последняя строка абзаца, не двухколоночный
    блок."""
    words = [
        _word_tuple("word", 35, 100, w=400),   # x1=435
        _word_tuple("tail", 560, 100, w=20),   # разрыв 435-560, центр=497.5 → 84% ширины
    ]
    zones = _detect_local_dual_zones(words, page_width=595)
    assert zones == []


def _make_single_column_pdf_with_footer() -> bytes:
    """Одноколоночный документ (абзацы во всю ширину, как в _make_single_column_pdf —
    нужно ДОСТАТОЧНО строк, чтобы слова абзацев перекрывали 35-65% зону чаще
    чем 2 раза почти на любом разрезе, иначе _detect_gutter поймает разрыв
    футера и на уровне страницы) плюс двухсторонняя строка подписи в футере."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
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
    page.insert_text((50, 760), "Customer_____________", fontsize=10)
    page.insert_text((400, 760), "Contractor____________", fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def test_parse_pdf_local_dual_zone_on_single_column_doc():
    """Одноколоночный документ с двухсторонней строкой в футере: doc.layout
    остаётся single_column (гутter не виден на уровне страницы — абзацы
    занимают всю ширину), но ParsedPage.dual_zones находит локальный разрыв
    футера. Регрессионный тест Fix-10.3."""
    pdf = _make_single_column_pdf_with_footer()
    doc = parse_pdf_bytes(pdf, filename="test.pdf")
    assert doc.layout == "single_column"
    assert len(doc.pages[0].dual_zones) >= 1
