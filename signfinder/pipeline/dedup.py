"""dedup_anchors — удаление дублей якорей. Перенос из Streamlit в core (v1.15).

Три прохода:
  1. Exact bbox — точный дубль по (page_hint, bbox) → оставляем первый
  2. Semantic   — одна страница + одинаковый text[:30] → якорь с "_" приоритетнее
  3. Underscore — на страницах с якорями "_" удаляем якоря без "_"

Работает с любым объектом имеющим атрибуты bbox, page_hint, anchor_text
(SimpleNamespace, TextAnchor, dict-proxy или dataclass).
"""
from __future__ import annotations

from collections import defaultdict


def dedup_anchors(anchors: list) -> list:
    """Убрать дубли якорей. Возвращает очищенный список."""
    if not anchors:
        return []

    # ── Шаг 1: точный дубль по bbox ──────────────────────────────────────────
    seen_bbox: set = set()
    step1: list = []
    for a in anchors:
        bbox = _bbox(a)
        key = (
            str(_attr(a, "page_hint", "0")),
            round(float(bbox[0]), 1),
            round(float(bbox[1]), 1),
            round(float(bbox[2]), 1),
            round(float(bbox[3]), 1),
        )
        if key not in seen_bbox:
            seen_bbox.add(key)
            step1.append(a)

    # ── Шаг 2: семантические дубли — одна страница, одинаковый text[:30] ─────
    # x_bucket: делим по 100pt — колонки обычно разнесены на 250-350pt
    groups: dict = defaultdict(list)
    for a in step1:
        text_key = (_attr(a, "anchor_text", "") or "")[:30]
        page_key = str(_attr(a, "page_hint", "0"))
        x_bucket = round(float(_bbox(a)[0]) / 100.0)
        groups[(page_key, text_key, x_bucket)].append(a)

    step2: list = []
    for group in groups.values():
        if len(group) == 1:
            step2.append(group[0])
            continue
        # Приоритет у якорей с подчёркиванием в тексте
        with_us = [a for a in group if "_" in (_attr(a, "anchor_text", "") or "")]
        if with_us:
            step2.append(with_us[0])
        else:
            # Иначе — якорь с наибольшим y1 (ниже на странице)
            step2.append(max(group, key=lambda a: float(_bbox(a)[3])))

    # ── Шаг 3: страницы с подчёркиваниями — убрать якоря без подчёркивания ──
    pages_with_underscore: set = {
        str(_attr(a, "page_hint", "0"))
        for a in step2
        if "_" in (_attr(a, "anchor_text", "") or "")
    }
    result: list = [
        a for a in step2
        if "_" in (_attr(a, "anchor_text", "") or "")
        or str(_attr(a, "page_hint", "0")) not in pages_with_underscore
    ]
    return result


# ── Helpers ──────────────────────────────────────────────────────────────────

def _attr(obj, name: str, default):
    """getattr с поддержкой dict."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _bbox(obj) -> list:
    raw = _attr(obj, "bbox", None)
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        return [float(x) for x in raw]
    return [0.0, 0.0, 0.0, 0.0]
