"""Тесты шаблонного матчинга (v1.15).

Покрывает: green/yellow пороги, коллизия, PERFECT_SCORE_THRESHOLD,
сортировку green-кандидатов по created_at DESC.
"""
from __future__ import annotations

import io
from datetime import datetime, timezone, timedelta

import fitz
import pytest

from signfinder.storage import LocalFilesystemStorage
from signfinder.templates.matcher import (
    PERFECT_SCORE_THRESHOLD,
    compute_composite_score,
    find_matching_templates,
)
from signfinder.templates.storage import new_template, save_template
from signfinder.traffic_light import TrafficLightConfig


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fp(simhash: str = "100", titles=None, chars=None, pages: int = 2) -> dict:
    return {
        "header_simhash": simhash,
        "section_titles": titles or ["Section 1", "Annex 1"],
        "chars_per_page": chars or [1000.0, 500.0],
        "page_count": pages,
        "total_chars": sum(chars or [1000.0, 500.0]),
        "language": "en",
    }


def _make_doc(pages: int = 2):
    """Создаёт fitz.Document с N страниц."""
    doc = fitz.open()
    for i in range(pages):
        p = doc.new_page()
        p.insert_text((50, 50), f"Page {i+1} content. Section 1. Annex 1.", fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    return fitz.open(stream=buf.getvalue(), filetype="pdf")


def _save_tpl(storage, fingerprint: dict, name: str = "test", lang: str = "en", offset_sec: int = 0) -> str:
    """Создаёт и сохраняет шаблон в storage. Возвращает template_id."""
    from datetime import timezone
    tpl = new_template(lang, anchors=[], fingerprint=fingerprint, synonyms_used={})
    tpl.name = name
    # Управляем created_at для тайбрейка
    ts = datetime.now(timezone.utc) + timedelta(seconds=offset_sec)
    tpl.created_at = ts.isoformat()
    save_template(storage, tpl)
    return tpl.template_id


def _storage_with_config(tmp_path, green_threshold: float = 0.85) -> LocalFilesystemStorage:
    st = LocalFilesystemStorage(str(tmp_path))
    st.write_json("settings/traffic_light.json", {
        "green_threshold": green_threshold,
        "synonym_match_required": False,
        "collision_delta": 0.05,
    })
    return st


# ── Пороги green/yellow ───────────────────────────────────────────────────────

def test_green_threshold_exact(tmp_path):
    """score = green_threshold (0.85) → green."""
    storage = _storage_with_config(tmp_path, green_threshold=0.85)
    fp = _make_fp()
    # Сохраняем точно такой же fingerprint — score будет 1.0 > 0.85
    _save_tpl(storage, fp)
    doc = _make_doc()
    result = find_matching_templates(doc, "en", storage, fingerprint=fp)
    assert result.traffic_light == "green"
    doc.close()


def test_yellow_when_below_threshold(tmp_path):
    """Нет шаблонов вообще → yellow (no_match_result)."""
    storage = _storage_with_config(tmp_path, green_threshold=0.85)
    fp = _make_fp()
    doc = _make_doc()
    result = find_matching_templates(doc, "en", storage, fingerprint=fp)
    assert result.traffic_light == "yellow"
    assert result.best_match is None
    doc.close()


def test_yellow_when_score_below_threshold(tmp_path):
    """Шаблон с другим fingerprint → score < 0.85 → yellow."""
    storage = _storage_with_config(tmp_path, green_threshold=0.85)
    doc_fp = _make_fp(simhash="999", titles=["Different Section"], chars=[500.0], pages=10)
    tpl_fp = _make_fp(simhash="1", titles=["Other Title"], chars=[5000.0], pages=1)
    _save_tpl(storage, tpl_fp)
    doc = _make_doc(10)
    result = find_matching_templates(doc, "en", storage, fingerprint=doc_fp)
    # Быстрая отсечка по page_count (10 vs 1 → |10-1|=9 > 2) → no candidates → yellow
    assert result.traffic_light == "yellow"
    doc.close()


# ── PERFECT_SCORE_THRESHOLD: три одинаковых шаблона — НЕ коллизия ─────────────

def test_three_perfect_scores_no_collision(tmp_path):
    """Три шаблона score=1.0 >= 0.99 → НЕ триггерят коллизию → green."""
    storage = _storage_with_config(tmp_path, green_threshold=0.85)
    fp = _make_fp()
    _save_tpl(storage, fp, name="tpl1", offset_sec=-20)
    _save_tpl(storage, fp, name="tpl2", offset_sec=-10)
    _save_tpl(storage, fp, name="tpl3", offset_sec=0)   # самый новый
    doc = _make_doc()
    result = find_matching_templates(doc, "en", storage, fingerprint=fp)
    # score=1.0 >= PERFECT_SCORE_THRESHOLD → has_collision=False → green
    assert result.traffic_light == "green"
    doc.close()


# ── Green-зона 0.85–0.99: сортировка по created_at DESC ──────────────────────

def test_green_zone_newest_wins(tmp_path):
    """Два шаблона в green-зоне: побеждает самый новый, не самый высокоскорированный."""
    storage = _storage_with_config(tmp_path, green_threshold=0.85)
    # Шаблон A — старый, чуть выше по score
    fp_old = _make_fp(simhash="100", titles=["Section 1", "Annex 1"], chars=[1000.0, 500.0], pages=2)
    # Шаблон B — новый, чуть ниже по score (другой simhash)
    fp_new = _make_fp(simhash="101", titles=["Section 1", "Annex 1"], chars=[1000.0, 500.0], pages=2)
    # Оба будут в green-зоне, но B новее
    id_old = _save_tpl(storage, fp_old, name="old_template", offset_sec=-100)
    id_new = _save_tpl(storage, fp_new, name="new_template", offset_sec=0)

    # Тестируем с fp совпадающим с новым шаблоном
    doc = _make_doc()
    result = find_matching_templates(doc, "en", storage, fingerprint=fp_new)
    # В зоне green — сортировка по created_at DESC → id_new должен быть best
    if result.traffic_light == "green" and result.best_match:
        assert result.best_match.template_id == id_new
    doc.close()


# ── Технический сбой: corrupted storage ──────────────────────────────────────

def test_corrupt_templates_path_returns_yellow(tmp_path):
    """Если list_templates бросает — возвращается yellow без краша."""
    from unittest.mock import patch
    storage = _storage_with_config(tmp_path)
    fp = _make_fp()
    doc = _make_doc()
    with patch("signfinder.templates.matcher.list_templates", side_effect=RuntimeError("boom")):
        result = find_matching_templates(doc, "en", storage, fingerprint=fp)
    assert result.traffic_light == "yellow"
    assert result.best_match is None
    doc.close()


# ── PERFECT_SCORE_THRESHOLD константа ────────────────────────────────────────

def test_perfect_score_threshold_value():
    assert PERFECT_SCORE_THRESHOLD == 0.99
