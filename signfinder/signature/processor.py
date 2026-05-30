"""
Signature pre-processing: any image (PNG/JPG/GIF) → clean RGBA PNG,
transparent background, cropped to ink, ready for overlay.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import cv2
import numpy as np
from PIL import Image

MAX_OUTPUT_WIDTH = 600


@dataclass
class SignatureProcessResult:
    png_bytes: bytes          # обработанная RGBA PNG
    confidence: float         # 0.0–1.0 уверенность что это подпись
    warnings: list[str]       # предупреждения оператору (не блокируют)
    bbox_original: tuple      # (x, y, w, h) где нашли на исходнике
    ink_coverage: float       # доля чернил в bbox (0.0–1.0)
    output_size: tuple        # (width, height) финального PNG
    input_size: tuple         # (width, height) исходника


def process_signature(image_bytes: bytes) -> SignatureProcessResult:
    """
    Обработать входной файл подписи.
    Вход: bytes (PNG/JPG/GIF).
    Выход: SignatureProcessResult с RGBA PNG и метриками.
    """
    warnings: list[str] = []
    confidence = 1.0

    # ── Шаг 1: Нормализация ──────────────────────────────────────────────────
    pil_img = Image.open(io.BytesIO(image_bytes))

    if getattr(pil_img, "is_animated", False):
        pil_img.seek(0)
        warnings.append("GIF: использован кадр 1")

    pil_rgba = pil_img.convert("RGBA")
    img_array = np.array(pil_rgba)
    input_h, input_w = img_array.shape[:2]

    bgr = cv2.cvtColor(img_array[:, :, :3], cv2.COLOR_RGB2BGR)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)

    # ── Шаг 2: Двойная детекция чернил ───────────────────────────────────────
    # A) HSV-маска — чёрные и синие чернила
    sat = hsv[:, :, 1].astype(np.int32)
    val = hsv[:, :, 2].astype(np.int32)
    hue = hsv[:, :, 0].astype(np.int32)

    black_mask = (sat < 80) & (val < 120)
    blue_mask  = (hue >= 100) & (hue <= 140) & (sat > 50) & (val < 180)
    hsv_mask   = (black_mask | blue_mask).astype(np.uint8) * 255

    # B) Адаптивный порог — для фото с неравномерным фоном
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    adaptive = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY_INV,
        blockSize=21, C=10,
    )

    combined_mask = cv2.bitwise_or(hsv_mask, adaptive)

    # ── Шаг 3: Морфологическая очистка ───────────────────────────────────────
    k_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    cleaned = cv2.morphologyEx(combined_mask, cv2.MORPH_OPEN,  k_open)
    cleaned = cv2.morphologyEx(cleaned,       cv2.MORPH_CLOSE, k_close)

    # ── Шаг 4: Bounding box ──────────────────────────────────────────────────
    nonzero = cv2.findNonZero(cleaned)
    if nonzero is None:
        empty_png = _make_empty_png(input_w, input_h)
        return SignatureProcessResult(
            png_bytes=empty_png,
            confidence=0.0,
            warnings=["Чернила не найдены"],
            bbox_original=(0, 0, input_w, input_h),
            ink_coverage=0.0,
            output_size=(input_w, input_h),
            input_size=(input_w, input_h),
        )

    bx, by, bw, bh = cv2.boundingRect(nonzero)
    pad = 12
    bx = max(0, bx - pad)
    by = max(0, by - pad)
    bw = min(input_w - bx, bw + 2 * pad)
    bh = min(input_h - by, bh + 2 * pad)
    bbox_original = (bx, by, bw, bh)

    # ── Шаг 5: Валидация ─────────────────────────────────────────────────────
    total_pixels = input_w * input_h
    ink_pixels   = int(np.sum(cleaned > 0))
    full_coverage = ink_pixels / total_pixels if total_pixels > 0 else 0.0

    bbox_area = bw * bh
    bbox_ink  = int(np.sum(cleaned[by:by+bh, bx:bx+bw] > 0))
    ink_coverage = bbox_ink / bbox_area if bbox_area > 0 else 0.0

    if full_coverage > 0.25:
        warnings.append("Высокая плотность чернил — возможно документ, не подпись")
        confidence -= 0.4

    if bh > 0 and (bw / bh) < 1.2:
        warnings.append("Нестандартные пропорции — обычно подпись шире высоты")
        confidence -= 0.2

    if bw > 0.85 * input_w and bh > 0.85 * input_h:
        warnings.append("Подпись занимает почти весь лист — возможно загружен документ")
        confidence -= 0.5

    confidence = float(np.clip(confidence, 0.0, 1.0))

    # ── Шаг 6: Кроп + прозрачный фон ────────────────────────────────────────
    crop_rgb  = img_array[by:by+bh, bx:bx+bw, :3].copy()
    crop_mask = cleaned[by:by+bh, bx:bx+bw].copy()

    alpha = cv2.GaussianBlur(crop_mask, (3, 3), 0)

    rgba_out = np.zeros((bh, bw, 4), dtype=np.uint8)
    rgba_out[:, :, :3] = crop_rgb
    rgba_out[:, :,  3] = alpha

    # ── Шаг 7: Downscale если нужно ─────────────────────────────────────────
    out_h, out_w = rgba_out.shape[:2]
    if out_w > MAX_OUTPUT_WIDTH:
        scale = MAX_OUTPUT_WIDTH / out_w
        new_w = MAX_OUTPUT_WIDTH
        new_h = max(1, int(out_h * scale))
        pil_out = Image.fromarray(rgba_out, "RGBA")
        pil_out = pil_out.resize((new_w, new_h), Image.LANCZOS)
        warnings.append(f"Изображение уменьшено с {out_w}×{out_h} до {new_w}×{new_h}")
        out_w, out_h = new_w, new_h
    else:
        pil_out = Image.fromarray(rgba_out, "RGBA")

    # ── Шаг 8: Сохранить как PNG ─────────────────────────────────────────────
    buf = io.BytesIO()
    pil_out.save(buf, format="PNG", optimize=True)
    png_bytes = buf.getvalue()

    return SignatureProcessResult(
        png_bytes=png_bytes,
        confidence=confidence,
        warnings=warnings,
        bbox_original=bbox_original,
        ink_coverage=ink_coverage,
        output_size=(out_w, out_h),
        input_size=(input_w, input_h),
    )


def _make_empty_png(w: int, h: int) -> bytes:
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
