"""Тесты Fix-14 — _add_reverse_underscore_patterns / _verify_reverse_underscore_matches.

Регрессия: LLM (run_step4) недетерминирован — может не сгенерировать reverse-
underscore паттерн на роль/юрлицо (`_{3,}...Заказчик`), даже когда футер
документа структурно требует его для нахождения места подписи. Проверено
сравнением двух прогонов на одном документе (см. CLAUDE.md v1.20.15).

Дизайн переработан относительно черновика задачи после эмпирической проверки
на реальном документе (signed_1ДоговорЛебедев.pdf, footer "Заказчик___
Подрядчик___" на одной строке):
  - `_{3,}[^\\n]{0,N}X` (однострочный) никогда не матчит на этом футере —
    подчёркивание и роль лежат в разных извлечённых PyMuPDF-строках даже при
    большом бюджете (подтверждено сохранённым debug_fix8_final_patterns.json:
    "не матчит — гипотеза кодера про [^\\n] vs [\\s\\S] проверяется").
  - Потребляющий `_{3,}[\\s\\S]{0,N}X` с бюджетом, достаточным чтобы
    перепрыгнуть разрыв, даёт bbox на всю ширину футера (_expand_line_bbox
    склеивает обе колонки через захваченный matched_text).
  - Итоговое решение — непотребляющий lookahead (`_{3,}(?=[\\s\\S]{0,150}X)`),
    что требует отдельной geometric-верификации (bbox реально содержит
    anchor), т.к. lookahead находит подчёркивание ОБЕИХ сторон footer'а.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import fitz
import pytest

from signfinder.pipeline.auto1 import (
    _add_reverse_underscore_patterns,
    _verify_reverse_underscore_matches,
)


# ── _add_reverse_underscore_patterns ──────────────────────────────────────────

def test_empty_our_side_returns_unchanged():
    patterns, pat_set = _add_reverse_underscore_patterns(["existing"], {})
    assert patterns == ["existing"]
    assert pat_set == set()


def test_none_our_side_returns_unchanged():
    patterns, pat_set = _add_reverse_underscore_patterns(["existing"], None)
    assert patterns == ["existing"]
    assert pat_set == set()


def test_role_generates_lookahead_pattern():
    patterns, pat_set = _add_reverse_underscore_patterns([], {"roles": ["Заказчик"]})
    assert len(pat_set) == 1
    p = next(iter(pat_set))
    assert p in patterns
    assert p.startswith("_{3,}(?=")
    assert "Заказчик" in p
    # Непотребляющий — обязательно lookahead, не голый [\s\S]/[^\n]
    assert "(?=" in p and p.endswith(")")


def test_legal_entity_generates_pattern_too():
    patterns, pat_set = _add_reverse_underscore_patterns([], {"legal_entity": "ООО Ромашка"})
    assert len(pat_set) == 1
    assert "Ромашка" in next(iter(pat_set)) or "ООО" in next(iter(pat_set))


def test_short_role_ignored():
    """Роли короче 4 символов игнорируются (как в _add_reverse_dot_patterns)."""
    patterns, pat_set = _add_reverse_underscore_patterns([], {"roles": ["ИП"]})
    assert pat_set == set()


def test_pattern_returned_in_set_even_if_already_present():
    """Если паттерн уже есть в final_patterns — не дублируется в списке, но
    остаётся в pattern_set (вызывающий код должен знать, что это
    reverse-underscore паттерн, для верификации/trusted_patterns)."""
    patterns, pat_set = _add_reverse_underscore_patterns([], {"roles": ["Заказчик"]})
    existing_pattern = next(iter(pat_set))
    patterns2, pat_set2 = _add_reverse_underscore_patterns([existing_pattern], {"roles": ["Заказчик"]})
    assert patterns2.count(existing_pattern) == 1  # не задвоился
    assert existing_pattern in pat_set2


def test_invalid_regex_escaped_safely():
    """re.escape защищает от спецсимволов в роли/юрлице — не должно падать."""
    patterns, pat_set = _add_reverse_underscore_patterns([], {"roles": ["Компания (ООО)"]})
    assert len(pat_set) == 1  # скомпилировался без ошибки


# ── _verify_reverse_underscore_matches ────────────────────────────────────────

def _make_two_column_pdf() -> bytes:
    """Buyer___ / Seller___ на одной строке — синтетический аналог футера
    'Заказчик___  Подрядчик___'. ASCII, т.к. PyMuPDF default font не рендерит
    кириллицу (see test_manual_anchor_reapply.py)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "Buyer", fontsize=12)
    page.insert_text((300, 100), "Seller", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _match(page, bbox, pattern):
    return SimpleNamespace(page=page, bbox=list(bbox), pattern=pattern)


def test_keeps_match_whose_bbox_contains_the_anchor():
    pdf_bytes = _make_two_column_pdf()
    fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    buyer_word = fitz_doc[0].get_text("words")[0]  # (x0,y0,x1,y1,'Buyer',...)
    fitz_doc.close()

    doc = SimpleNamespace(pdf_bytes=pdf_bytes)
    m = _match(0, (buyer_word[0], buyer_word[1], buyer_word[2], buyer_word[3]), "rev_pat")
    result = _verify_reverse_underscore_matches([m], doc, {"roles": ["Buyer"]}, {"rev_pat"})
    assert result == [m]


def test_drops_match_whose_bbox_does_not_contain_the_anchor():
    """Симметрично реальному багу: bbox лежит на 'чужой' колонке (Seller),
    хотя паттерн генерировался для нашей роли (Buyer) — должен отфильтроваться."""
    pdf_bytes = _make_two_column_pdf()
    fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    seller_word = fitz_doc[0].get_text("words")[1]
    fitz_doc.close()

    doc = SimpleNamespace(pdf_bytes=pdf_bytes)
    m = _match(0, (seller_word[0], seller_word[1], seller_word[2], seller_word[3]), "rev_pat")
    result = _verify_reverse_underscore_matches([m], doc, {"roles": ["Buyer"]}, {"rev_pat"})
    assert result == []


def test_non_reverse_underscore_pattern_passes_through_unchecked():
    """Матчи от других паттернов не трогает — даже если bbox геометрически
    не содержит anchor (не наша забота, чужая логика)."""
    pdf_bytes = _make_two_column_pdf()
    fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    seller_word = fitz_doc[0].get_text("words")[1]
    fitz_doc.close()

    doc = SimpleNamespace(pdf_bytes=pdf_bytes)
    m = _match(0, (seller_word[0], seller_word[1], seller_word[2], seller_word[3]), "some_other_pattern")
    result = _verify_reverse_underscore_matches([m], doc, {"roles": ["Buyer"]}, {"rev_pat"})
    assert result == [m]


def test_empty_reverse_underscore_pats_short_circuits():
    m = _match(0, (0, 0, 10, 10), "rev_pat")
    result = _verify_reverse_underscore_matches([m], SimpleNamespace(pdf_bytes=b""), {"roles": ["Buyer"]}, set())
    assert result == [m]


def test_empty_matches_returns_empty():
    result = _verify_reverse_underscore_matches([], SimpleNamespace(pdf_bytes=b""), {"roles": ["Buyer"]}, {"p"})
    assert result == []
