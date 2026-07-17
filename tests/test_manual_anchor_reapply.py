"""Тесты Fix-12.3 — apply_template_anchors: текстовая привязка для manual_click.

До Fix-12 повторное применение ручного якоря всегда садилось на примороженный
абсолютный bbox, записанный в момент клика — если документ B (тот же шаблон,
но чуть другой реflow: абзац на строку длиннее/короче) физически сдвигал
текст, подпись оставалась на старом месте и "уезжала" от текста-ориентира.

Fix-12 хранит в TextAnchor.context_before JSON {anchor_bbox, offset_dx,
offset_dy} (см. SignfinderLand's _confirmPlacer/toggleRememberTemplate) — при
реаппликации сначала ищем anchor_text на новой странице и переносим то же
смещение, откатываясь на голый bbox только если текст не нашёлся или данных
о смещении нет (старые шаблоны, сохранённые до Fix-12).

Синтетические PDF используют ASCII-метки: PyMuPDF's default base-14 font
(insert_text без явного fontfile) не рендерит кириллицу — get_text()
возвращает replacement-символы, и search_for() ничего не находит независимо
от логики Fix-12. Сама привязка (текстовый поиск + смещение) от языка не
зависит — реальная кириллица уже покрыта на живых документах (Fix-10.3).
"""
from __future__ import annotations

import io
import json
from types import SimpleNamespace

import fitz
import pytest

from signfinder.anchors.finder import apply_template_anchors
from signfinder.pdf.parser import parse_pdf_bytes


def _make_pdf(label_xy: tuple[float, float], label: str = "Buyer") -> bytes:
    """Однострочный синтетический PDF с меткой в заданной позиции."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text(label_xy, label, fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _make_pdf_two_occurrences(y_first: float, y_second: float, label: str = "Buyer") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((100, y_first), label, fontsize=12)
    page.insert_text((100, y_second), label, fontsize=12)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _manual_anchor(bbox, anchor_text="", context_before="", page_hint="0"):
    return {
        "id": "manual_1",
        "anchor_level": 1,
        "anchor_text": anchor_text,
        "position": "on",
        "generated_pattern": "",
        "bbox": list(bbox),
        "added_by": "manual_click",
        "page_hint": page_hint,
        "context_before": context_before,
    }


def test_text_search_resolves_new_position_when_text_shifted():
    """Текст-ориентир сдвинулся на 30pt вниз (реflow) — подпись должна
    сдвинуться вместе с ним на то же смещение, а не остаться на старом месте."""
    # "Buyer" на исходном документе стоит в y=100, оператор поставил подпись
    # в (ax0+30, ay0-5) -> offset_dx=30, offset_dy=-5 от найденного слова.
    orig_pdf = _make_pdf((100, 100))
    orig_words = fitz.open(stream=orig_pdf, filetype="pdf")[0].get_text("words")
    ax0, ay0 = orig_words[0][0], orig_words[0][1]
    placed_bbox = (ax0 + 30, ay0 - 5, ax0 + 30 + 120, ay0 - 5 + 30)
    probe = json.dumps({"anchor_bbox": [ax0, ay0, orig_words[0][2], orig_words[0][3]],
                         "offset_dx": 30.0, "offset_dy": -5.0})
    anchor = _manual_anchor(placed_bbox, anchor_text="Buyer", context_before=probe)

    shifted_pdf = _make_pdf((100, 130))  # текст сдвинут на 30pt вниз
    doc = parse_pdf_bytes(shifted_pdf, filename="b.pdf")
    template = SimpleNamespace(anchors=[anchor], name="tpl")

    matches = apply_template_anchors(doc, template)
    assert len(matches) == 1
    m = matches[0]
    assert m.added_by == "manual_click"

    new_words = fitz.open(stream=shifted_pdf, filetype="pdf")[0].get_text("words")
    nx0, ny0 = new_words[0][0], new_words[0][1]
    assert m.bbox[0] == pytest.approx(nx0 + 30.0, abs=0.5)
    assert m.bbox[1] == pytest.approx(ny0 - 5.0, abs=0.5)
    # Ширина/высота сохраняются из исходного размещённого bbox, не пересчитываются
    assert (m.bbox[2] - m.bbox[0]) == pytest.approx(120.0, abs=0.5)
    assert (m.bbox[3] - m.bbox[1]) == pytest.approx(30.0, abs=0.5)


def test_falls_back_to_frozen_bbox_when_text_not_found():
    """anchor_text не встречается в новом документе вообще — старое поведение:
    голый абсолютный bbox как есть."""
    placed_bbox = (130.0, 95.0, 250.0, 125.0)
    probe = json.dumps({"anchor_bbox": [100.0, 89.0, 180.0, 101.0],
                         "offset_dx": 30.0, "offset_dy": -5.0})
    anchor = _manual_anchor(placed_bbox, anchor_text="NoSuchWord", context_before=probe)

    other_pdf = _make_pdf((100, 100), label="Totally different document")
    doc = parse_pdf_bytes(other_pdf, filename="c.pdf")
    template = SimpleNamespace(anchors=[anchor], name="tpl")

    matches = apply_template_anchors(doc, template)
    assert len(matches) == 1
    assert tuple(matches[0].bbox) == placed_bbox


def test_old_template_without_context_before_uses_frozen_bbox():
    """Обратная совместимость: шаблон, сохранённый до Fix-12, не несёт
    context_before (пустая строка) — reapply работает как раньше, без ошибок."""
    placed_bbox = (130.0, 95.0, 250.0, 125.0)
    anchor = _manual_anchor(placed_bbox, anchor_text="Buyer", context_before="")

    pdf = _make_pdf((100, 130))  # текст сдвинут — но без context_before это не важно
    doc = parse_pdf_bytes(pdf, filename="d.pdf")
    template = SimpleNamespace(anchors=[anchor], name="tpl")

    matches = apply_template_anchors(doc, template)
    assert len(matches) == 1
    assert tuple(matches[0].bbox) == placed_bbox


def test_malformed_context_before_json_falls_back_gracefully():
    """context_before есть, но не валидный JSON — не должно падать, откат на bbox."""
    placed_bbox = (10.0, 20.0, 40.0, 50.0)
    anchor = _manual_anchor(placed_bbox, anchor_text="Buyer", context_before="{not json")

    pdf = _make_pdf((100, 100))
    doc = parse_pdf_bytes(pdf, filename="e.pdf")
    template = SimpleNamespace(anchors=[anchor], name="tpl")

    matches = apply_template_anchors(doc, template)
    assert len(matches) == 1
    assert tuple(matches[0].bbox) == placed_bbox


def test_disambiguates_multiple_occurrences_by_proximity_to_original():
    """Слово встречается дважды на странице — должен выбраться экземпляр,
    ближайший к исходной позиции текста-ориентира (anchor_bbox из probe)."""
    orig_pdf = _make_pdf_two_occurrences(y_first=100, y_second=500)
    orig_words = fitz.open(stream=orig_pdf, filetype="pdf")[0].get_text("words")
    first = min(orig_words, key=lambda w: w[1])  # тот что выше на странице (y=100)
    ax0, ay0 = first[0], first[1]

    placed_bbox = (ax0 + 10, ay0 + 10, ax0 + 130, ay0 + 40)
    probe = json.dumps({"anchor_bbox": [ax0, ay0, first[2], first[3]],
                         "offset_dx": 10.0, "offset_dy": 10.0})
    anchor = _manual_anchor(placed_bbox, anchor_text="Buyer", context_before=probe)

    # В новом документе оба вхождения сдвинуты на +5pt каждое — экземпляр
    # ближе к исходному (y=100 -> y=105) должен победить, а не (y=500 -> y=505)
    new_pdf = _make_pdf_two_occurrences(y_first=105, y_second=505)
    doc = parse_pdf_bytes(new_pdf, filename="f.pdf")
    template = SimpleNamespace(anchors=[anchor], name="tpl")

    matches = apply_template_anchors(doc, template)
    assert len(matches) == 1
    new_words = fitz.open(stream=new_pdf, filetype="pdf")[0].get_text("words")
    near_word = min(new_words, key=lambda w: w[1])  # тот что выше (y≈105)
    assert matches[0].bbox[1] == pytest.approx(near_word[1] + 10.0, abs=0.5)
