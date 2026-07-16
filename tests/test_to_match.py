"""Тесты SignFinder._to_match (Fix-10.4) — провенанс added_by при TextAnchor -> SignMatch.

Регрессия: manual_match_to_anchor (SignMatch -> TextAnchor, anchors/finder.py)
уже сохранял added_by='manual_click' с v1.18.3 ("без этого провенанс терялся
... подпись «уезжала» на regex-линию"). Но обратное направление — _to_match
(TextAnchor -> SignMatch, вызывается из SignFinder.sign() для каждого якоря
из anchors_json) — этот провенанс терял: SignMatch.added_by по умолчанию
"auto_regex", и _to_match не передавал item.added_by явно. Любой manual_click
якорь, дошедший до sign() через TextAnchor (а не напрямую как SignMatch —
т.е. любой случай КРОМЕ самой первой живой ручной простановки через
manual_anchors_json), приходил в apply_signature() как auto_regex и терял
и точный bbox (падал в текстовый поиск линии), и размер (падал на
DEFAULT_SIGNATURE_HEIGHT_PT вместо ширины/высоты нарисованного вручную
прямоугольника).
"""
from __future__ import annotations

import pytest

from signfinder import SignFinder
from signfinder.anchors.models import SignMatch, TextAnchor


def _text_anchor(added_by: str, page_hint: str = "2", bbox=(10.0, 20.0, 130.0, 60.0)) -> TextAnchor:
    return TextAnchor(
        id="a1",
        anchor_type="text_proximity",
        anchor_level=1,
        anchor_text="ctx",
        position="on",
        offset_pt=0.0,
        generated_pattern="",
        context_before="",
        context_after="",
        page_hint=page_hint,
        added_by=added_by,
        added_at="2026-01-01T00:00:00+00:00",
        bbox=bbox,
    )


def test_manual_click_anchor_preserves_added_by():
    """Регрессия: ручной якорь, пришедший как TextAnchor, должен остаться
    manual_click в результирующем SignMatch — иначе apply_signature() не
    возьмёт bbox напрямую и подпись потеряет и размер, и позицию."""
    anchor = _text_anchor("manual_click", bbox=(10.0, 20.0, 150.0, 55.0))
    match = SignFinder._to_match(anchor)
    assert match.added_by == "manual_click"
    assert tuple(match.bbox) == (10.0, 20.0, 150.0, 55.0)


def test_auto_regex_anchor_stays_auto_regex():
    anchor = _text_anchor("auto_regex")
    match = SignFinder._to_match(anchor)
    assert match.added_by == "auto_regex"


def test_signmatch_passthrough_unchanged():
    """SignMatch на входе возвращается как есть (включая свой added_by)."""
    m = SignMatch(id="m1", page=0, bbox=(0, 0, 10, 10), context="", party="p",
                  pattern="", added_by="manual_exact")
    result = SignFinder._to_match(m)
    assert result is m
    assert result.added_by == "manual_exact"


@pytest.mark.parametrize("page_hint,expected_page", [("first", 0), ("last", -1), ("3", 3), ("bad", 0)])
def test_page_hint_resolution(page_hint, expected_page):
    anchor = _text_anchor("auto_regex", page_hint=page_hint)
    match = SignFinder._to_match(anchor)
    assert match.page == expected_page


def test_invalid_type_raises():
    with pytest.raises(TypeError):
        SignFinder._to_match({"not": "a valid type"})
