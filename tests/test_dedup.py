"""Тесты dedup_anchors (v1.15).

Покрывает все три шага дедупликации на синтетических наборах якорей.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from signfinder.pipeline.dedup import dedup_anchors


def _a(text: str, page: str, bbox=(0.0, 10.0, 100.0, 20.0)) -> SimpleNamespace:
    """Создать минимальный якорь-заглушку."""
    return SimpleNamespace(
        id=f"{page}_{text[:8]}",
        anchor_text=text,
        page_hint=page,
        bbox=list(bbox),
    )


# ── Шаг 1: Exact bbox ────────────────────────────────────────────────────────

def test_step1_exact_bbox_dedup():
    """Два якоря с одинаковым bbox на одной странице → остаётся один."""
    a1 = _a("Lessor", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    a2 = _a("Lessor", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    result = dedup_anchors([a1, a2])
    assert len(result) == 1


def test_step1_different_bbox_kept():
    """Разные bbox + разный text → оба сохраняются (Шаг 1, exact bbox)."""
    a1 = _a("Lessor signature", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    a2 = _a("Lessee signature", "1", bbox=(50.0, 200.0, 200.0, 215.0))
    result = dedup_anchors([a1, a2])
    assert len(result) == 2


def test_step1_different_pages_kept():
    """Одинаковый bbox, разные страницы → оба сохраняются."""
    a1 = _a("Sign", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    a2 = _a("Sign", "2", bbox=(50.0, 100.0, 200.0, 115.0))
    result = dedup_anchors([a1, a2])
    assert len(result) == 2


# ── Шаг 2: Semantic dedup с приоритетом "_" ──────────────────────────────────

def test_step2_underscore_wins_over_no_underscore():
    """На одной странице, одинаковый text[:30]: якорь с '_' побеждает."""
    a1 = _a("ЛЕССОР", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    a2 = _a("ЛЕССОР_____", "1", bbox=(50.0, 200.0, 200.0, 215.0))
    result = dedup_anchors([a1, a2])
    assert len(result) == 1
    assert "_" in (result[0].anchor_text or "")


def test_step2_same_text_different_pages_both_kept():
    """Одинаковый text[:30], но разные страницы → оба остаются."""
    a1 = _a("ЛЕССОР", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    a2 = _a("ЛЕССОР", "2", bbox=(50.0, 100.0, 200.0, 115.0))
    result = dedup_anchors([a1, a2])
    assert len(result) == 2


# ── Шаг 3: Underscore-приоритет на уровне страницы ───────────────────────────

def test_step3_removes_non_underscore_when_underscore_on_page():
    """Страница 2 с одним '_'-якорем и одним обычным → обычный удаляется."""
    a_us = _a("_________", "2", bbox=(50.0, 100.0, 200.0, 115.0))
    a_no = _a("визиты сторон:", "2", bbox=(50.0, 200.0, 200.0, 215.0))
    result = dedup_anchors([a_us, a_no])
    assert len(result) == 1
    assert "_" in result[0].anchor_text


def test_step3_keeps_non_underscore_on_page_without_underscore():
    """Страница без '_'-якорей: обычный якорь остаётся."""
    a_no = _a("Lessor:", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    result = dedup_anchors([a_no])
    assert len(result) == 1


def test_step3_multiple_pages():
    """Страница 1 без '_', страница 2 с '_': на стр.1 обычный остаётся, на стр.2 только '_'."""
    a1_no = _a("Lessor:", "1", bbox=(50.0, 100.0, 200.0, 115.0))
    a2_us = _a("________", "2", bbox=(50.0, 100.0, 200.0, 115.0))
    a2_no = _a("визиты:", "2", bbox=(50.0, 200.0, 200.0, 215.0))
    result = dedup_anchors([a1_no, a2_us, a2_no])
    page_hints = [r.page_hint for r in result]
    assert "1" in page_hints
    assert "2" in page_hints
    # На стр.2 только якорь с подчёркиванием
    page2 = [r for r in result if r.page_hint == "2"]
    assert len(page2) == 1
    assert "_" in page2[0].anchor_text


# ── Кейс: 42 якоря из адресного блока ─────────────────────────────────────────

def test_address_block_overload():
    """42 одинаковых 'адресных' якоря → должно остаться 1–2."""
    anchors = []
    base_text = "город Москва, ул. Ленина"
    for i in range(42):
        anchors.append(_a(base_text, "1", bbox=(50.0, 50.0 + i * 0.1, 200.0, 65.0 + i * 0.1)))
    result = dedup_anchors(anchors)
    # После step1 (разные bbox по y) останется 42, после step2 (одинаковый text[:30]) → 1
    assert len(result) <= 2


# ── Крайние случаи ────────────────────────────────────────────────────────────

def test_empty_list():
    assert dedup_anchors([]) == []


def test_single_anchor_unchanged():
    a = _a("Lessor ______", "1")
    result = dedup_anchors([a])
    assert len(result) == 1


def test_dict_anchor_supported():
    """dedup_anchors работает с dict-объектами."""
    a = {"anchor_text": "______", "page_hint": "1", "bbox": [50.0, 100.0, 200.0, 115.0]}
    result = dedup_anchors([a, a])
    assert len(result) == 1


# ── Dedup для dual-column (v1.18.7) ──────────────────────────────────────────

def test_dual_column_same_text_different_x_kept():
    """'Vadim Borisov' в левой колонке (x=50) и правой (x=350) → ОБА остаются.

    Регрессионный тест: в EN+PL документах одинаковый текст в двух колонках
    не должен схлопываться (фикс x_bucket в Шаге 2)."""
    left  = _a("Vadim Borisov", "1", bbox=(50.0, 600.0, 200.0, 615.0))
    right = _a("Vadim Borisov", "1", bbox=(350.0, 600.0, 500.0, 615.0))
    result = dedup_anchors([left, right])
    assert len(result) == 2


def test_dual_column_dups_within_column_dedup():
    """Два 'Borisov' в ОДНОЙ колонке (x=50, разный y) → схлопываются."""
    a1 = _a("Borisov", "1", bbox=(50.0, 600.0, 200.0, 615.0))
    a2 = _a("Borisov", "1", bbox=(50.0, 700.0, 200.0, 715.0))   # тот же x0 → тот же x_bucket
    result = dedup_anchors([a1, a2])
    assert len(result) == 1
