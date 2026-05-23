"""Рендер страниц PDF с подсветкой найденных мест подписи."""
from __future__ import annotations

import io

try:
    import fitz
except ImportError:
    fitz = None  # type: ignore[assignment]
from PIL import Image, ImageDraw


def render_page_with_highlights(
    pdf_bytes: bytes,
    page_num: int,
    matches_on_page: list,
    scale: float = 1.5,
) -> bytes:
    """Рендерит страницу PDF в PNG, рисует прямоугольники вокруг bbox.

    matches_on_page — list[SignMatch] для конкретной страницы.
    operator_excluded → серая рамка, иначе красная.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page = doc[page_num]
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    draw = ImageDraw.Draw(img)

    for m in matches_on_page:
        x0, y0, x1, y1 = m.bbox
        rect = (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
        color = "gray" if getattr(m, "operator_excluded", False) else "red"
        draw.rectangle(rect, outline=color, width=3)

    doc.close()
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
