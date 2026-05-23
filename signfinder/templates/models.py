"""Модели templates-подсистемы."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class DocumentTemplate:
    """Шаблон документа со списком якорей и fingerprint."""
    template_id: str
    name: str
    language: str
    created_at: str
    created_by: str                  # "pipeline_auto_1" | "manual_enrichment"

    fingerprint: dict
    anchors: list                    # list[dict] — сериализованные TextAnchor
    synonyms_used: dict              # legal_entity, roles, signer

    usage_stats: dict = field(default_factory=lambda: {
        "times_applied": 0,
        "times_confirmed": 0,
        "times_rejected": 0,
        "last_used": None,
    })


@dataclass
class MatchResult:
    """Результат матчинга одного шаблона с документом."""
    template_id: str
    template_name: str
    score: float
    score_breakdown: dict
    explanation: str
    synonyms_match: bool


@dataclass
class MatcherResult:
    """Итог матчинга: светофор + best + кандидаты."""
    traffic_light: Literal["green", "yellow"]
    best_match: Optional[MatchResult]
    all_candidates: list
    explanation: str
