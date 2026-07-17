"""Тесты _filter_by_our_side_context (Fix-10.3, geometric-переработка Fix-14.1)
— zone-aware фильтр "не наша сторона".

Fix-10.3: реверс-паттерн вида `_{3,}[\\s\\S]{0,50}Заказчик` на однокол­оночном
документе задваивался — совпадал и с "нашей" линией, и с линией контрагента в
той же строке футера — потому что _detect_gutter (весь документ разом) не
видит локальный разрыв футера на фоне слов основного текста, и фильтр раньше
пропускался целиком для single_column (v1.18.21 правило). Фильтр стал точечным:
либо вся страница dual_column_vertical (старое поведение), либо матч физически
попадает в локальную зону из ParsedPage.dual_zones (см. test_parser_columns.py,
_detect_local_dual_zones).

Fix-14.1: для local_dual_zone матчей текстовая проверка "80 символов до"
заменена на geometric (page.get_textbox(bbox) содержит наш синоним?) —
подтверждено на двух реальных документах, что текстовая позиция там не
работает: повторяющийся футер/шапка извлекается PDF-парсером РАНЬШЕ текста
тела страницы, так что "текст перед матчем" не значит "текст выше на
странице". Функция теперь принимает doc (нужен doc.pdf_bytes для
get_textbox), не просто список страниц.
"""
from __future__ import annotations

import io
from types import SimpleNamespace

import fitz

from signfinder.pdf.parser import ParsedPage
from signfinder.pipeline.auto1 import _filter_by_our_side_context

OUR_SIDE = {"legal_entity": "ооо ромашка", "roles": ["заказчик"], "signer": "иванов"}


def _match(page: int, bbox, context: str = "", pattern: str = "p1") -> SimpleNamespace:
    return SimpleNamespace(page=page, bbox=list(bbox), context=context, pattern=pattern)


def _doc(pages, pdf_bytes=b""):
    return SimpleNamespace(pages=pages, pdf_bytes=pdf_bytes)


def test_match_outside_any_zone_not_filtered():
    """single_column страница, матч ВНЕ dual_zones — фильтр его не трогает,
    даже если рядом нет ни одного нашего синонима (нечего фильтровать).
    Эта ветка не открывает PDF — pdf_bytes может быть пустым."""
    page = ParsedPage(page_num=0, text="какой-то нейтральный текст без сторон",
                       layout="single_column", dual_zones=[])
    m = _match(0, (35, 100, 200, 115), "какой-то нейтральный текст без сторон")
    result = _filter_by_our_side_context([m], _doc([page]), OUR_SIDE)
    assert result == [m]


def test_dual_column_vertical_page_filters_without_local_zone():
    """Старое поведение сохранено: страница целиком dual_column_vertical —
    текстовая проверка работает по всей странице, даже без записи в
    dual_zones. Эта ветка тоже не трогает geometric/PDF — идёт напрямую в
    текстовую проверку (page_is_dual, не local_zone)."""
    text = "какой-то текст без наших синонимов впереди подрядчик блок текста"
    page = ParsedPage(page_num=0, text=text, layout="dual_column_vertical", dual_zones=[])
    m = _match(0, (35, 100, 200, 115), "подрядчик блок текста")
    result = _filter_by_our_side_context([m], _doc([page]), OUR_SIDE)
    assert result == []


def test_trusted_pattern_bypasses_zone_check():
    """signer_pats (доверенные) не фильтруются, даже находясь в зоне —
    проверяется до зоны/geometric, PDF не открывается."""
    page = ParsedPage(page_num=0, text="что угодно", layout="single_column",
                       dual_zones=[(100.0, 115.0, 280.0)])
    m = _match(0, (410, 100, 549, 115), pattern="signer_pat")
    result = _filter_by_our_side_context([m], _doc([page]), OUR_SIDE, trusted_patterns={"signer_pat"})
    assert result == [m]


def test_no_our_side_returns_matches_unchanged():
    m = _match(0, (35, 100, 200, 115), "текст")
    result = _filter_by_our_side_context([m], _doc([ParsedPage(page_num=0, text="текст")]), {})
    assert result == [m]


def test_empty_matches_returns_empty():
    assert _filter_by_our_side_context([], _doc([]), OUR_SIDE) == []


# ── Fix-14.1: geometric-проверка для local_dual_zone ──────────────────────────
# ASCII-метки: PyMuPDF default font не рендерит кириллицу через insert_text
# (see test_manual_anchor_reapply.py) — логика проверки от языка не зависит.

def _make_two_column_pdf():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((50, 100), "Buyer", fontsize=12)
    page.insert_text((300, 100), "Seller", fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _word_bbox(pdf_bytes, index):
    fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    w = fitz_doc[0].get_text("words")[index]
    fitz_doc.close()
    return (w[0], w[1], w[2], w[3])


ASCII_OUR_SIDE = {"legal_entity": "", "roles": ["buyer"], "signer": ""}


def test_local_zone_geometric_keeps_match_containing_our_synonym():
    pdf_bytes = _make_two_column_pdf()
    buyer_bbox = _word_bbox(pdf_bytes, 0)
    page = ParsedPage(page_num=0, text="Buyer Seller", layout="single_column",
                       dual_zones=[(buyer_bbox[1] - 2, buyer_bbox[3] + 2, 275.0)])
    m = _match(0, buyer_bbox)
    result = _filter_by_our_side_context([m], _doc([page], pdf_bytes), ASCII_OUR_SIDE)
    assert result == [m]


def test_local_zone_geometric_drops_match_not_containing_our_synonym():
    """Симметрично реальному багу: bbox лежит на чужой колонке (Seller) —
    geometric-проверка находит "seller", не "buyer", фильтрует."""
    pdf_bytes = _make_two_column_pdf()
    seller_bbox = _word_bbox(pdf_bytes, 1)
    page = ParsedPage(page_num=0, text="Buyer Seller", layout="single_column",
                       dual_zones=[(seller_bbox[1] - 2, seller_bbox[3] + 2, 275.0)])
    m = _match(0, seller_bbox)
    result = _filter_by_our_side_context([m], _doc([page], pdf_bytes), ASCII_OUR_SIDE)
    assert result == []


def test_local_zone_geometric_ignores_stale_text_context():
    """Ключевая регрессия Fix-14.1: page.text специально сделан "неправильным"
    (как в реальных документах — футер извлекается раньше тела) — старая
    текстовая проверка на нём провалилась бы, но geometric её не использует
    для local_zone и всё равно даёт верный результат."""
    pdf_bytes = _make_two_column_pdf()
    buyer_bbox = _word_bbox(pdf_bytes, 0)
    # page.text не содержит вообще ничего похожего на реальный контент —
    # имитирует "текст перед матчем ничего не значит" сценарий
    page = ParsedPage(page_num=0, text="", layout="single_column",
                       dual_zones=[(buyer_bbox[1] - 2, buyer_bbox[3] + 2, 275.0)])
    m = _match(0, buyer_bbox, context="")
    result = _filter_by_our_side_context([m], _doc([page], pdf_bytes), ASCII_OUR_SIDE)
    assert result == [m]
