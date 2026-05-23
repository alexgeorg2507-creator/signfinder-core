"""Модели anchor-системы: TextAnchor и SignMatch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass
class TextAnchor:
    """Единица привязки подписи к тексту документа (v1.7+).

    anchor_level: 1 — близкий клик, 4 — fallback на подчёркивание.
    added_by: 'auto_regex' (создан пайплайном) | 'manual_click' (создан кликом).
    """
    id: str
    anchor_type: Literal["text_proximity", "underline_line"]
    anchor_level: int                                   # 1–4
    anchor_text: str                                    # точный текст из документа
    position: Literal["right", "left", "below", "above", "on"]
    offset_pt: float                                    # смещение от anchor_text до подписи
    generated_pattern: str                              # regex для finder.py
    context_before: str                                 # ~50 символов до якоря
    context_after: str                                  # ~50 символов после
    page_hint: str                                      # "first"|"last"|"any"|str(idx)
    added_by: Literal["auto_regex", "manual_click"]
    added_at: str                                       # ISO timestamp UTC
    bbox: tuple[float, float, float, float]             # (x0,y0,x1,y1) место подписи


@dataclass
class SignMatch:
    """Найденное место подписи в документе (regex или template anchor)."""
    id: str
    page: int                                           # 0-indexed
    bbox: tuple                                         # (x0, y0, x1, y1) в пунктах
    context: str                                        # текст вокруг найденного места
    party: str                                          # имя стороны
    pattern: str                                        # какой паттерн сработал
    confidence: float = 0.0
    status: str = "candidate"
    correction_applied: Optional[str] = None
    operator_excluded: bool = False
