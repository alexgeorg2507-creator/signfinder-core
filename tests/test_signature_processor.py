"""Тесты signature processor — детерминированные части (v1.15)."""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from signfinder.signature.processor import MAX_OUTPUT_WIDTH, process_signature


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_png(width: int, height: int, color=(0, 0, 0, 255), mode="RGBA") -> bytes:
    """Создать PNG заданного размера и цвета."""
    img = Image.new(mode, (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_signature_png(width: int = 400, height: int = 100) -> bytes:
    """Белый лист с чёрной 'подписью' (полоса посередине)."""
    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    pixels = img.load()
    # Нарисовать чёрную горизонтальную линию
    for x in range(50, width - 50):
        for y in range(height // 3, 2 * height // 3):
            pixels[x, y] = (0, 0, 0, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Crop bbox с padding не выходит за края ────────────────────────────────────

def test_crop_bbox_no_out_of_bounds():
    """crop bbox + padding не выходит за пределы изображения."""
    png = _make_signature_png(100, 50)
    result = process_signature(png)
    ow, oh = result.output_size
    assert ow > 0
    assert oh > 0
    # bbox_original не выходит за края input
    bx, by, bw, bh = result.bbox_original
    iw, ih = result.input_size
    assert bx >= 0 and by >= 0
    assert bx + bw <= iw
    assert by + bh <= ih


# ── ink_coverage = ink_pixels / total_bbox_pixels ────────────────────────────

def test_ink_coverage_range():
    """ink_coverage в диапазоне [0, 1]."""
    png = _make_signature_png()
    result = process_signature(png)
    assert 0.0 <= result.ink_coverage <= 1.0


def test_ink_coverage_empty_image():
    """Полностью белое изображение → ink_coverage близко к 0, confidence=0."""
    white = _make_png(200, 80, color=(255, 255, 255, 255))
    result = process_signature(white)
    assert result.confidence == 0.0
    assert result.ink_coverage == 0.0


# ── Валидация: широкая подпись на весь лист → warning ────────────────────────

def test_validation_full_sheet_warning():
    """Когда bbox занимает почти весь лист → warning о документе."""
    # Рисуем чёрный прямоугольник на 90%+ площади
    width, height = 200, 100
    img = Image.new("RGBA", (width, height), (255, 255, 255, 255))
    pixels = img.load()
    for x in range(5, width - 5):
        for y in range(5, height - 5):
            pixels[x, y] = (0, 0, 0, 255)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = process_signature(buf.getvalue())
    # Должно быть предупреждение о документе или плотности
    assert len(result.warnings) > 0 or result.confidence < 1.0


# ── Downscale: 1200px → 600px ────────────────────────────────────────────────

def test_downscale_wide_image():
    """Изображение шириной > MAX_OUTPUT_WIDTH → результат ≤ MAX_OUTPUT_WIDTH."""
    png = _make_signature_png(width=1200, height=200)
    result = process_signature(png)
    out_w, out_h = result.output_size
    assert out_w <= MAX_OUTPUT_WIDTH


def test_downscale_preserves_aspect():
    """Пропорции bbox чернил сохраняются при уменьшении."""
    png = _make_signature_png(width=1200, height=400)
    result = process_signature(png)
    # _make_signature_png рисует чернила только в средней трети по Y.
    # Сравниваем aspect ИТОГОВОГО PNG с aspect BBOX ЧЕРНИЛ, не исходника.
    bx, by, bw, bh = result.bbox_original
    expected_aspect = bw / bh if bh > 0 else 1.0
    out_w, out_h = result.output_size
    actual_aspect = out_w / out_h if out_h > 0 else 0
    # Допуск 5% — округление при downscale до 600px
    assert abs(actual_aspect - expected_aspect) / expected_aspect < 0.05


def test_small_image_not_upscaled():
    """Изображение меньше MAX_OUTPUT_WIDTH не растягивается."""
    png = _make_signature_png(width=200, height=60)
    result = process_signature(png)
    out_w, _ = result.output_size
    assert out_w <= 200


# ── MAX_OUTPUT_WIDTH константа ────────────────────────────────────────────────

def test_max_output_width_value():
    assert MAX_OUTPUT_WIDTH == 600


# ── Валидность результата ─────────────────────────────────────────────────────

def test_result_is_valid_png():
    """Результат — валидный PNG."""
    png = _make_signature_png()
    result = process_signature(png)
    # Должен открываться PIL
    img = Image.open(io.BytesIO(result.png_bytes))
    assert img.mode == "RGBA"


def test_result_fields_present():
    """Все поля SignatureProcessResult заполнены."""
    png = _make_signature_png()
    result = process_signature(png)
    assert isinstance(result.confidence, float)
    assert isinstance(result.warnings, list)
    assert isinstance(result.ink_coverage, float)
    assert len(result.output_size) == 2
    assert len(result.input_size) == 2
    assert len(result.bbox_original) == 4
