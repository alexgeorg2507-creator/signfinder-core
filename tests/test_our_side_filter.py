"""Тесты _filter_by_our_side_context (Fix-10.3) — zone-aware фильтр "не наша сторона".

Регрессия: реверс-паттерн вида `_{3,}[\\s\\S]{0,50}Заказчик` на однокол­оночном
документе задваивался — совпадал и с "нашей" линией, и с линией контрагента в
той же строке футера — потому что _detect_gutter (весь документ разом) не
видит локальный разрыв футера на фоне слов основного текста, и фильтр раньше
пропускался целиком для single_column (v1.18.21 правило). Теперь фильтр
применяется точечно: либо вся страница dual_column_vertical (старое
поведение), либо матч физически попадает в локальную зону из
ParsedPage.dual_zones (см. test_parser_columns.py, _detect_local_dual_zones).
"""
from __future__ import annotations

from types import SimpleNamespace

from signfinder.pdf.parser import ParsedPage
from signfinder.pipeline.auto1 import _filter_by_our_side_context

OUR_SIDE = {"legal_entity": "ооо ромашка", "roles": ["заказчик"], "signer": "иванов"}


def _match(page: int, bbox, context: str, pattern: str = "p1") -> SimpleNamespace:
    return SimpleNamespace(page=page, bbox=list(bbox), context=context, pattern=pattern)


def test_match_outside_any_zone_not_filtered():
    """single_column страница, матч ВНЕ dual_zones — фильтр его не трогает,
    даже если рядом нет ни одного нашего синонима (нечего фильтровать)."""
    page = ParsedPage(page_num=0, text="какой-то нейтральный текст без сторон",
                       layout="single_column", dual_zones=[])
    m = _match(0, (35, 100, 200, 115), "какой-то нейтральный текст без сторон")
    result = _filter_by_our_side_context([m], [page], OUR_SIDE)
    assert result == [m]


def test_match_in_local_zone_without_our_side_filtered_out():
    """Матч попадает в локальную зону футера, но перед ним (80 символов
    назад) нет наших синонимов — это блок Подрядчика → фильтруется."""
    text = "текст текст подрядчик______________________________ заказчик"
    page = ParsedPage(page_num=0, text=text, layout="single_column",
                       dual_zones=[(100.0, 115.0, 280.0)])
    m = _match(0, (410, 100, 549, 115), "подрядчик______________________________ заказчик")
    result = _filter_by_our_side_context([m], [page], OUR_SIDE)
    assert result == []


def test_match_in_local_zone_with_our_side_kept():
    """Та же зона, но перед якорем есть наш синоним → остаётся."""
    text = "ооо ромашка заказчик______________________________ подрядчик"
    page = ParsedPage(page_num=0, text=text, layout="single_column",
                       dual_zones=[(100.0, 115.0, 280.0)])
    m = _match(0, (35, 100, 200, 115), "заказчик______________________________ подрядчик")
    result = _filter_by_our_side_context([m], [page], OUR_SIDE)
    assert result == [m]


def test_dual_column_vertical_page_filters_without_local_zone():
    """Старое поведение сохранено: страница целиком dual_column_vertical —
    фильтр работает по всей странице, даже без записи в dual_zones."""
    text = "какой-то текст без наших синонимов впереди подрядчик блок текста"
    page = ParsedPage(page_num=0, text=text, layout="dual_column_vertical", dual_zones=[])
    m = _match(0, (35, 100, 200, 115), "подрядчик блок текста")
    result = _filter_by_our_side_context([m], [page], OUR_SIDE)
    assert result == []


def test_trusted_pattern_bypasses_zone_check():
    """signer_pats (доверенные — заякорены на ФИО подписанта) не фильтруются,
    даже находясь в зоне без наших синонимов рядом."""
    text = "подрядчик______________________________ заказчик"
    page = ParsedPage(page_num=0, text=text, layout="single_column",
                       dual_zones=[(100.0, 115.0, 280.0)])
    m = _match(0, (410, 100, 549, 115),
               "подрядчик______________________________ заказчик", pattern="signer_pat")
    result = _filter_by_our_side_context([m], [page], OUR_SIDE, trusted_patterns={"signer_pat"})
    assert result == [m]


def test_no_our_side_returns_matches_unchanged():
    m = _match(0, (35, 100, 200, 115), "текст")
    result = _filter_by_our_side_context([m], [ParsedPage(page_num=0, text="текст")], {})
    assert result == [m]


def test_empty_matches_returns_empty():
    assert _filter_by_our_side_context([], [], OUR_SIDE) == []
