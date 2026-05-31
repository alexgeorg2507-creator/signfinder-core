"""Наложение PNG-подписи на PDF, маркер места подписи, опциональный flatten. v1.14.0"""
from __future__ import annotations

import io

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
SIGNATURE_X_OFFSET_PT = 20


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


def _find_underscore_anchor(page, bbox, pattern: str):
    """Найти позицию подчёркиваний для размещения подписи."""
    x0, y0, x1, y1 = bbox
    line_height = y1 - y0

    if pattern.startswith("_"):
        return x0 + SIGNATURE_X_OFFSET_PT, y1, line_height

    underscore_rects = page.search_for("___")

    best = None
    best_dist = float("inf")
    for r in underscore_rects:
        if r.y1 < y0 - 2 or r.y0 > y1 + 2:
            continue
        if r.x0 < x0 - 10 or r.x0 > x1:
            continue
        rc = (r.y0 + r.y1) / 2
        bc = (y0 + y1) / 2
        d = abs(rc - bc)
        if d < best_dist:
            best_dist = d
            best = r

    if best:
        return best.x0 + SIGNATURE_X_OFFSET_PT, best.y1, max(line_height, best.height)

    bbox_width = x1 - x0
    return x0 + bbox_width * 0.3, y1, line_height


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
