"""Тесты моделей anchors."""
from __future__ import annotations

from dataclasses import asdict

from signfinder.anchors import SignMatch, TextAnchor


def test_text_anchor_serialization():
    anchor = TextAnchor(
        id="abc123",
        anchor_type="text_proximity",
        anchor_level=1,
        anchor_text="Арендатор",
        position="right",
        offset_pt=10.0,
        generated_pattern=r"Арендатор\s*_{3,}",
        context_before="блок ",
        context_after=" /ФИО/",
        page_hint="last",
        added_by="manual_click",
        added_at="2026-05-23T10:00:00+00:00",
        bbox=(100.0, 200.0, 300.0, 220.0),
    )
    data = asdict(anchor)
    assert data["id"] == "abc123"
    assert data["bbox"] == (100.0, 200.0, 300.0, 220.0)

    # Round-trip
    restored = TextAnchor(**data)
    assert restored == anchor


def test_sign_match_defaults():
    m = SignMatch(
        id="sig_001",
        page=2,
        bbox=(0.0, 0.0, 100.0, 50.0),
        context="...Арендатор____...",
        party="Арендатор",
        pattern=r"Арендатор\s*_{3,}",
    )
    assert m.confidence == 0.0
    assert m.status == "candidate"
    assert m.correction_applied is None
    assert m.operator_excluded is False


def test_sign_match_dict_roundtrip():
    m = SignMatch(
        id="sig_001", page=0, bbox=(0, 0, 1, 1),
        context="ctx", party="X", pattern="p", confidence=0.9,
    )
    data = asdict(m)
    restored = SignMatch(**data)
    assert restored == m


def test_build_anchor_from_regex_match():
    from signfinder.anchors import build_anchor_from_regex_match

    anchor = build_anchor_from_regex_match(
        pattern=r"Арендатор\s*_{3,}",
        match_text="Арендатор ___________________",
        match_bbox=(50.0, 100.0, 250.0, 120.0),
        page_idx=3,
        language="ru",
        context_before="Сторона 2: ",
        context_after=" /Иванов/",
    )
    assert anchor.added_by == "auto_regex"
    assert anchor.anchor_type == "text_proximity"
    assert anchor.bbox == (50.0, 100.0, 250.0, 120.0)
    assert anchor.page_hint == "3"
    assert anchor.context_before == "Сторона 2: "
    assert anchor.context_after == " /Иванов/"
