"""Наложение PNG-подписи на PDF, маркер места подписи, опциональный flatten. v1.14.0"""
from __future__ import annotations

import io
import re

try:
    import fitz
except ImportError:
    fitz = None  # type: ignore[assignment]
from PIL import Image


# Целевая высота подписи в pt (15мм × 2.835 pt/mm ≈ 42pt)
DEFAULT_SIGNATURE_HEIGHT_PT = 42
MAX_SIGNATURE_HEIGHT_PT = 85
MIN_SIGNATURE_HEIGHT_PT = 20

# Горизонтальный сдвиг от левого края подчёркивания (pt)
# 0 = подпись начинается точно от левого края подчёркивания (после trim белых полей PNG)
SIGNATURE_X_OFFSET_PT = 0


def apply_signature(
    pdf_bytes: bytes,
    matches: list,
    png_bytes: bytes | None,
    flatten: bool = False,
    scale: float = 1.0,
    use_signature: bool = True,
    use_marker: bool = False,
    marker_color: str = "pink",
    sign_above_line: bool = False,
) -> bytes:
    """Наложить PNG подписи и/или маркер места подписи на PDF.

    matches — list[SignMatch] из anchors.models.
    png_bytes — может быть None если use_signature=False.
    use_signature — вставлять PNG подпись.
    use_marker   — рисовать прямоугольный маркер на правом поле (4×12мм).
    marker_color — "pink" (255,182,193) | "gray" (180,180,180).
    scale — мультипликатор размера подписи (1.0 = 42pt).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    # Подготовить PNG один раз — только если нужен
    img_stream: bytes | None = None
    sig_h, sig_w = 0.0, 0.0
    if use_signature and png_bytes:
        img = Image.open(io.BytesIO(png_bytes))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        # Кроп по alpha-каналу: убираем прозрачные поля для корректного aspect ratio.
        # Итоговый RGBA передаётся в PyMuPDF напрямую (alpha обрабатывается нативно).
        _, _, _, a_ch = img.split()
        content_box = a_ch.getbbox()
        if content_box:
            img = img.crop(content_box)
        png_w, png_h = img.size
        aspect = png_w / png_h if png_h else 1.0
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img_stream = buf.getvalue()
        sig_h = min(
            max(MIN_SIGNATURE_HEIGHT_PT, DEFAULT_SIGNATURE_HEIGHT_PT * scale),
            MAX_SIGNATURE_HEIGHT_PT,
        )
        sig_w = sig_h * aspect

    for m in matches:
        if getattr(m, "operator_excluded", False) or getattr(m, "status", "") == "rejected_by_llm":
            continue

        page = doc[m.page]
        anchor_x, anchor_y_bottom, _ = _find_underscore_anchor(page, m.bbox, m.pattern, above_line=sign_above_line)
        bbox = list(m.bbox)  # [x0, y0, x1, y1]

        # PNG подпись
        if use_signature and img_stream is not None:
            # Лёгкий заход подписи за линию вниз (~15% высоты, не более 6pt)
            descender = min(sig_h * 0.15, 6.0)
            sig_rect = fitz.Rect(
                anchor_x,
                anchor_y_bottom - sig_h + descender,
                anchor_x + sig_w,
                anchor_y_bottom + descender,
            )
            page.insert_image(sig_rect, stream=img_stream, keep_proportion=True)

        # Маркер: ~4×12мм прямоугольник на правом поле, выровнен по центру строки якоря
        if use_marker:
            pw = page.rect.width
            y_center = (bbox[1] + bbox[3]) / 2
            marker_rect = fitz.Rect(
                pw - 14.0,
                y_center - 17.0,
                pw - 3.0,
                y_center + 17.0,
            )
            fill = (1.0, 0.714, 0.757) if marker_color != "gray" else (0.706, 0.706, 0.706)
            page.draw_rect(marker_rect, fill=fill, color=None, width=0)

    out_bytes = doc.tobytes(deflate=True)
    doc.close()

    if flatten:
        out_bytes = _flatten_pdf(out_bytes)

    return out_bytes


def _extract_literal_prefix(pattern: str) -> str:
    """Извлечь литеральный текстовый префикс паттерна до первого спецсимвола.

    'Заказчик[\\s_]{0,50}_{3,}' → 'Заказчик'
    '_{3,}'                      → ''
    '\\(Лебедев'                 → ''
    """
    special = frozenset(r'[]()\\.+*?{}^$|')
    result = []
    for ch in pattern:
        if ch in special:
            break
        result.append(ch)
    return ''.join(result).strip()


def _find_line_below(page, x: float, y_from: float, x_tol: float = 80.0,
                     y_max_gap: float = 60.0):
    """Найти Y нижнего края ближайшей линии подписи ниже точки (x, y_from).

    Ищет '___' и графические линии (drawings) в колонке x ± x_tol,
    ниже y_from но не дальше y_max_gap. Возвращает y нижнего края линии или None.
    """
    candidates = []

    # Текстовые подчёркивания
    try:
        for r in page.search_for("___"):
            if r.y0 >= y_from - 2 and (r.y0 - y_from) <= y_max_gap:
                rc = (r.x0 + r.x1) / 2
                if abs(rc - x) <= x_tol or (r.x0 <= x <= r.x1):
                    candidates.append(r.y1)
    except Exception:
        pass

    # Графические линии (DocuSign рисует линии как vector drawings)
    try:
        for d in page.get_drawings():
            rect = d.get("rect")
            if rect is None:
                continue
            # тонкая горизонтальная линия: высота мала, ширина заметна
            h = rect.y1 - rect.y0
            w = rect.x1 - rect.x0
            if h <= 3 and w >= 30:
                if rect.y0 >= y_from - 2 and (rect.y0 - y_from) <= y_max_gap:
                    rc = (rect.x0 + rect.x1) / 2
                    if abs(rc - x) <= x_tol or (rect.x0 <= x <= rect.x1):
                        candidates.append(rect.y1)
    except Exception:
        pass

    if not candidates:
        return None
    # Ближайшая линия (минимальный y, т.е. сразу под тегом)
    return min(candidates)


def _find_underscore_anchor(page, bbox, pattern: str, above_line: bool = False):
    """Найти позицию подчёркиваний для размещения подписи.

    Логика приоритетов:
    1. Паттерн начинается с '_' → используем x0 bbox + offset.
    2. Паттерн имеет текстовый префикс (например 'Заказчик') → ищем его
       на странице, берём правый край (x1) — это начало зоны подписи.
       Надёжнее rawdict т.к. не зависит от кодировки символов в PDF.
    3. Rawdict char-level — fallback.
    4. search_for("___") — fallback.
    5. Пропорциональный сдвиг от x0 — последний резерв.

    above_line=True: возвращает y0 линии вместо y1 (подпись ставится НАД линией).
    """
    x0, y0, x1, y1 = bbox
    line_height = y1 - y0
    y_center = (y0 + y1) / 2

    # 0. DocuSign-тег рядом с якорем — ставим точно на тег.
    # Тег \tN\ \sN\ \eN\ — текстовое слово с координатами. blacklist \dN\ (дата),
    # \aN\ (инициалы). Срабатывает ТОЛЬКО если теги реально есть в тексте —
    # для русских/обычных документов words не содержит \xN\ и проваливается в case 1.
    try:
        words = page.get_text("words")  # (x0,y0,x1,y1,text,...)
        ds_tags = [w for w in words if re.match(r'\\[tse]\d+\\', w[4])]  # t/s/e, НЕ d/a
        if ds_tags:
            # Тег должен быть в ТОЙ ЖЕ колонке, что и якорь (X-близость), иначе
            # правый матч в двухколоночном подвале прыгнул бы на левый тег.
            bbox_xc = (x0 + x1) / 2
            col_tol = max(80.0, x1 - x0)  # допуск по X = ширина якоря, мин 80pt
            best_tag = None
            best_score = float("inf")
            for w in ds_tags:
                tag_xc = (w[0] + w[2]) / 2
                tag_yc = (w[1] + w[3]) / 2
                dy = abs(tag_yc - y_center)
                dx = abs(tag_xc - bbox_xc)
                if dy < 150 and dx <= col_tol:  # та же колонка, окно 150pt по y
                    score = dy + dx
                    if score < best_score:
                        best_score = score
                        best_tag = w
            if best_tag is not None:
                tag_x = best_tag[0]
                tag_bottom = best_tag[3]
                tag_h = best_tag[3] - best_tag[1]
                # Тег — метка НАД линией подписи. Ищем реальную линию ниже тега
                # в той же колонке, сажаем подпись на неё.
                line_y = _find_line_below(page, tag_x, tag_bottom, x_tol=col_tol)
                if line_y is not None:
                    y_pos = (line_y - tag_h) if above_line else line_y
                    return tag_x, y_pos, tag_h
                # Линия не найдена — опустить подпись ниже тега на ~1.5 высоты тега
                y_pos = (tag_bottom - tag_h) if above_line else tag_bottom + tag_h * 1.5
                return tag_x, y_pos, tag_h
    except Exception:
        pass

    # 1. Паттерн начинается с подчёркивания ИЛИ с точечной линии (\. = \.{5,}...).
    # LLM может генерировать (?:_{3,}...) — убираем (?:...) перед проверкой.
    # Для обратных паттернов (линия стоит ДО названия: ".....\nInnowise") bbox
    # из _find_signature_bbox уже клипирован к нужной колонке — x0 корректен.
    _pat_norm = re.sub(r'^\(\?:', '', pattern)
    if _pat_norm.startswith("_") or _pat_norm.startswith("\\."):
        y_pos = y0 if above_line else y1
        return x0 + SIGNATURE_X_OFFSET_PT, y_pos, line_height

    # 2. Текстовый префикс роли (напр. "Заказчик") — самый надёжный метод:
    #    находим текст на странице, берём его правый край rr.x1
    prefix = _extract_literal_prefix(pattern)
    if len(prefix) >= 2:
        for rr in page.search_for(prefix):
            rr_yc = (rr.y0 + rr.y1) / 2
            if abs(rr_yc - y_center) < 5 and rr.x0 >= x0 - 5:
                y_pos = y0 if above_line else y1
                return rr.x1 + SIGNATURE_X_OFFSET_PT, y_pos, line_height

    # 3. Rawdict char-level — точная позиция символа '_'
    try:
        data = page.get_text("rawdict", flags=0)
        for block in data.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    for ch in span.get("chars", []):
                        if ch.get("c") != "_":
                            continue
                        cb = ch.get("bbox", (0, 0, 0, 0))
                        ch_yc = (cb[1] + cb[3]) / 2
                        if ch_yc < y0 - 2 or ch_yc > y1 + 2:
                            continue
                        if cb[0] < x0 - 5 or cb[0] > x1:
                            continue
                        cb_y = float(cb[1]) if above_line else float(cb[3])
                        return float(cb[0]) + SIGNATURE_X_OFFSET_PT, cb_y, line_height
    except Exception:
        pass

    # 4. search_for("___") — fallback для нестандартных PDF
    underscore_rects = page.search_for("___")
    best = None
    best_dist = float("inf")
    for r in underscore_rects:
        if r.y1 < y0 - 2 or r.y0 > y1 + 2:
            continue
        if r.x0 < x0 - 10 or r.x0 > x1:
            continue
        d = abs((r.y0 + r.y1) / 2 - y_center)
        if d < best_dist:
            best_dist = d
            best = r
    if best:
        y_pos = best.y0 if above_line else best.y1
        return best.x0 + SIGNATURE_X_OFFSET_PT, y_pos, max(line_height, best.height)

    # 5. Последний резерв.
    # Если паттерн начинается с '_' — подчёркивание графическое, но x0 bbox корректен.
    # Используем x0 + offset, а не пропорцию (которая смещала бы вправо на ~40pt).
    y_pos = y0 if above_line else y1
    if _pat_norm.startswith("_"):
        return x0 + SIGNATURE_X_OFFSET_PT, y_pos, line_height
    # Иначе — паттерн вида "Роль____", x0 = текстовый блок, сдвигаем к хвосту.
    return x0 + (x1 - x0) * 0.3, y_pos, line_height



def _split_rgba_png(img: Image.Image) -> tuple[bytes, bytes | None]:
    """Разделить PIL Image на RGB-поток PNG и альфа-маску PNG."""
    if img.mode == "RGBA":
        r, g, b, a = img.split()
        rgb_img = Image.merge("RGB", (r, g, b))
        buf_rgb = io.BytesIO()
        rgb_img.save(buf_rgb, format="PNG")

        buf_mask = io.BytesIO()
        a.save(buf_mask, format="PNG")

        return buf_rgb.getvalue(), buf_mask.getvalue()

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG")
    return buf.getvalue(), None


def _flatten_pdf(pdf_bytes: bytes) -> bytes:
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst = fitz.open()
    for page in src:
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        new_page = dst.new_page(width=page.rect.width, height=page.rect.height)
        new_page.insert_image(page.rect, pixmap=pix)
    out = dst.tobytes(deflate=True)
    src.close()
    dst.close()
    return out
