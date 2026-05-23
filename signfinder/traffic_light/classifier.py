"""Светофор шаблонов — классификация green/yellow по score и синонимам.

ПЕРЕНОС из core/traffic_light.py с заменой прямых GCS-вызовов на storage abstraction.
"""
from __future__ import annotations

import sys
from dataclasses import asdict, dataclass
from typing import Literal, Optional

from signfinder.storage.base import StorageBackend
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

_CONFIG_BLOB = "traffic_light_config.json"


@dataclass
class TrafficLightConfig:
    green_threshold: float = 0.95
    synonym_match_required: bool = True
    collision_delta: float = 0.05  # разница score < delta → коллизия


def classify(
    score: float,
    synonyms_match: bool,
    has_collision: bool = False,
    config: Optional[TrafficLightConfig] = None,
) -> Literal["green", "yellow"]:
    """Классификация по светофору.

    Зелёный требует ВСЕ условия:
      - score >= green_threshold
      - synonyms_match (если synonym_match_required)
      - has_collision == False
    """
    if config is None:
        config = TrafficLightConfig()

    if score < config.green_threshold:
        return "yellow"
    if config.synonym_match_required and not synonyms_match:
        return "yellow"
    if has_collision:
        return "yellow"
    return "green"


def load_config(storage: Optional[StorageBackend] = None) -> TrafficLightConfig:
    """Загружает конфиг из storage. При отсутствии storage или файла — дефолт."""
    if storage is None:
        return TrafficLightConfig()
    try:
        data = storage.read_json(_CONFIG_BLOB)
        if data:
            return TrafficLightConfig(**data)
    except Exception as e:
        logger.warning("load_config failed, using defaults: %s", e)
        sys.stderr.write(f"[traffic_light] load_config: {e}\n")
    return TrafficLightConfig()


def save_config(storage: StorageBackend, config: TrafficLightConfig) -> None:
    """Сохраняет конфиг в storage."""
    try:
        storage.write_json(_CONFIG_BLOB, asdict(config))
    except Exception as e:
        logger.error("save_config failed: %s", e)
        sys.stderr.write(f"[traffic_light] save_config: {e}\n")
        raise
