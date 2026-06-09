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
SIGNATURE_X_OFFSET_PT = -12


def apply_signature(
    pdf_bytes: bytes,
    matches: list,
    png_bytes: bytes | None,
    flatten: bool = False,
    scale: float = 1.0,
    use_signature: bool = True,
    use_marker: bool = False,
    marker_color: str = "pink",
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
    img_rgb, mask_bytes = None, None
    sig_h, sig_w = 0.0, 0.0
    if use_signature and png_bytes:
        img = Image.open(io.BytesIO(png_bytes))
        png_w, png_h = img.size
        aspect = png_w / png_h if png_h else 1.0
        img_rgb, mask_bytes = _split_rgba_png(img)
        sig_h = min(
            max(MIN_SIGNATURE_HEIGHT_PT, DEFAULT_SIGNATURE_HEIGHT_PT * scale),
            MAX_SIGNATURE_HEIGHT_PT,
        )
        sig_w = sig_h * aspect

    for m in matches:
        if getattr(m, "operator_excluded", False) or getattr(m, "status", "") == "rejected_by_llm":
            continue

        page = doc[m.page]
        anchor_x, anchor_y_bottom, _ = _find_underscore_anchor(page, m.bbox, m.pattern)
        bbox = list(m.bbox)  # [x0, y0, x1, y1]

        # PNG подпись
        if use_signature and img_rgb is not None:
            sig_rect = fitz.Rect(
                anchor_x,
                anchor_y_bottom - sig_h,
                anchor_x + sig_w,
                anchor_y_bottom,
            )
            page.insert_image(sig_rect, stream=img_rgb, mask=mask_bytes, keep_proportion=True)

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


def _find_underscore_anchor(page, bbox, pattern: str):
    """Найти позицию подчёркиваний для размещения подписи.

    Логика приоритетов:
    1. Паттерн начинается с '_' → используем x0 bbox + offset.
    2. Паттерн имеет текстовый префикс (например 'Заказчик') → ищем его
       на странице, берём правый край (x1) — это начало зоны подписи.
       Надёжнее rawdict т.к. не зависит от кодировки символов в PDF.
    3. Rawdict char-level — fallback.
    4. search_for("___") — fallback.
    5. Пропорциональный сдвиг от x0 — последний резерв.
    """
    x0, y0, x1, y1 = bbox
    line_height = y1 - y0
    y_center = (y0 + y1) / 2

    # 1. Паттерн сам начинается с подчёркивания (с учётом non-capturing group обёртки).
    # LLM может генерировать (?:_{3,}...) — убираем (?:...) перед проверкой.
    _pat_norm = re.sub(r'^\(\?:', '', pattern)
    if _pat_norm.startswith("_"):
        return x0 + SIGNATURE_X_OFFSET_PT, y1, line_height

    # 2. Текстовый префикс роли (напр. "Заказчик") — самый надёжный метод:
    #    находим текст на странице, берём его правый край rr.x1
    prefix = _extract_literal_prefix(pattern)
    if len(prefix) >= 2:
        for rr in page.search_for(prefix):
            rr_yc = (rr.y0 + rr.y1) / 2
            if abs(rr_yc - y_center) < 5 and rr.x0 >= x0 - 5:
                return rr.x1 + SIGNATURE_X_OFFSET_PT, y1, line_height

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
                        return float(cb[0]) + SIGNATURE_X_OFFSET_PT, y1, line_height
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
        return best.x0 + SIGNATURE_X_OFFSET_PT, best.y1, max(line_height, best.height)

    # 5. Последний резерв.
    # Если паттерн начинается с '_' — подчёркивание графическое, но x0 bbox корректен.
    # Используем x0 + offset, а не пропорцию (которая смещала бы вправо на ~40pt).
    if _pat_norm.startswith("_"):
        return x0 + SIGNATURE_X_OFFSET_PT, y1, line_height
    # Иначе — паттерн вида "Роль____", x0 = текстовый блок, сдвигаем к хвосту.
    return x0 + (x1 - x0) * 0.3, y1, line_height


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
