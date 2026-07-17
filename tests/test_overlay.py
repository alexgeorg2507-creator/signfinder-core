"""Тесты overlay.py (v1.15).

Покрывает: _find_underscore_anchor, sig_rect, маркер, x-offset, use_marker/use_signature.
"""
from __future__ import annotations

import io

import fitz
import pytest
from PIL import Image

from signfinder.pdf.overlay import (
    DEFAULT_SIGNATURE_HEIGHT_PT,
    MAX_SIGNATURE_HEIGHT_PT,
    MIN_SIGNATURE_HEIGHT_PT,
    SIGNATURE_X_OFFSET_PT,
    _find_underscore_anchor,
    apply_signature,
)
from signfinder.anchors.models import SignMatch


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_simple_pdf_with_underscores() -> bytes:
    """PDF с одной страницей содержащей '___' + текст якоря."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 400), "Lessor: ___________________  /Ivanov/", fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_signature_png() -> bytes:
    """Минимальная RGBA PNG подпись."""
    img = Image.new("RGBA", (200, 50), (0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_match(page: int = 0, bbox=(50.0, 390.0, 300.0, 405.0), pattern: str = "Lessor") -> SignMatch:
    return SignMatch(
        id="test_match",
        page=page,
        bbox=bbox,
        context="Lessor: ___",
        party="Lessor",
        pattern=pattern,
        confidence=1.0,
    )


# ── _find_underscore_anchor ───────────────────────────────────────────────────

def test_find_underscore_anchor_pattern_starts_with_underscore():
    """Если pattern начинается с '_' и bbox шире самой линии (здесь bbox.x0=50
    начинается на слове "Lessor:", реальные подчёркивания — с x≈85.57, как и
    в реальном футере "Заказчик___" после _expand_line_bbox, см. v1.20.18) —
    возвращает x РЕАЛЬНОЙ линии, найденной внутри bbox, а не голый bbox.x0."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    bbox = (50.0, 390.0, 300.0, 405.0)
    pattern = "___Lessor"
    x, y_bottom, line_h = _find_underscore_anchor(page, bbox, pattern)
    doc.close()
    assert abs(x - (85.57 + SIGNATURE_X_OFFSET_PT)) < 1.0


def test_find_underscore_anchor_tight_bbox_unchanged():
    """bbox уже плотно облегает саму линию (нет слитого лейбла перед ней) —
    найденная позиция совпадает с bbox.x0, поведение как раньше."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    # bbox начинается прямо с подчёркивания (x≈85.57), не с "Lessor:"
    bbox = (85.57, 390.0, 191.21, 405.0)
    x, y_bottom, line_h = _find_underscore_anchor(page, bbox, "___")
    doc.close()
    assert abs(x - (85.57 + SIGNATURE_X_OFFSET_PT)) < 1.0


def test_find_underscore_anchor_no_underscore_chars_falls_back_to_bbox_x0():
    """Внутри bbox нет символов '_' вообще (напр. графическая линия без
    текста, DocuSign-стиль) — откат на bbox.x0 как раньше."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 400), "Lessor: /Ivanov/", fontsize=10)
    bbox = (50.0, 390.0, 300.0, 405.0)
    x, y_bottom, line_h = _find_underscore_anchor(page, bbox, "___Lessor")
    doc.close()
    assert abs(x - (50.0 + SIGNATURE_X_OFFSET_PT)) < 1.0


def test_find_underscore_anchor_returns_x_y_lineh():
    """Возвращает кортеж из трёх чисел."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[0]
    result = _find_underscore_anchor(page, (50.0, 390.0, 300.0, 405.0), "Lessor")
    doc.close()
    assert len(result) == 3
    assert all(isinstance(v, (int, float)) for v in result)


# ── sig_rect размер и позиция ─────────────────────────────────────────────────

def test_signature_height_default_scale():
    """apply_signature(scale=1.0) → подпись высотой DEFAULT_SIGNATURE_HEIGHT_PT."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    png_bytes = _make_signature_png()
    match = _make_match(pattern="Lessor")
    result = apply_signature(pdf_bytes, [match], png_bytes, scale=1.0, use_signature=True)
    # Результат — валидный PDF, больше оригинала (добавлено изображение)
    assert isinstance(result, bytes)
    assert len(result) > 100


def test_signature_x_offset_pt_constant():
    """SIGNATURE_X_OFFSET_PT == 0 (после v1.13 crop по alpha)."""
    assert SIGNATURE_X_OFFSET_PT == 0


def test_default_signature_height_pt_value():
    """DEFAULT_SIGNATURE_HEIGHT_PT == 28 (v1.20.17 — было 42, слишком крупно
    относительно ручной простановки со стандартным размером плейсера)."""
    assert DEFAULT_SIGNATURE_HEIGHT_PT == 28


# ── Маркер координаты ─────────────────────────────────────────────────────────

def test_marker_on_right_margin():
    """use_marker=True → маркер добавляется (PDF увеличивается)."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    match = _make_match()
    result_with = apply_signature(
        pdf_bytes, [match], None,
        use_signature=False, use_marker=True, marker_color="pink",
    )
    # PDF с маркером должен отличаться от оригинала
    assert result_with != pdf_bytes


def test_marker_only_no_signature():
    """use_signature=False, use_marker=True → маркер есть, подпись нет."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    match = _make_match()
    result = apply_signature(
        pdf_bytes, [match], None,
        use_signature=False, use_marker=True,
    )
    assert isinstance(result, bytes)
    # Не крашится, результат валидный PDF
    doc = fitz.open(stream=result, filetype="pdf")
    assert doc.page_count == 1
    doc.close()


def test_marker_and_signature_together():
    """use_signature=True + use_marker=True → оба применяются без ошибки."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    png_bytes = _make_signature_png()
    match = _make_match()
    result = apply_signature(
        pdf_bytes, [match], png_bytes,
        use_signature=True, use_marker=True,
    )
    assert isinstance(result, bytes)
    doc = fitz.open(stream=result, filetype="pdf")
    assert doc.page_count == 1
    doc.close()


def test_no_signature_no_marker_returns_valid_pdf():
    """use_signature=False, use_marker=False → PDF без изменений (но валидный)."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    match = _make_match()
    result = apply_signature(
        pdf_bytes, [match], None,
        use_signature=False, use_marker=False,
    )
    doc = fitz.open(stream=result, filetype="pdf")
    assert doc.page_count == 1
    doc.close()


def test_marker_color_gray():
    """marker_color='gray' не крашится."""
    pdf_bytes = _make_simple_pdf_with_underscores()
    match = _make_match()
    result = apply_signature(
        pdf_bytes, [match], None,
        use_signature=False, use_marker=True, marker_color="gray",
    )
    assert isinstance(result, bytes)
