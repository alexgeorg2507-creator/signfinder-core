"""Светофор шаблонов: классификация green/yellow."""
from signfinder.traffic_light.classifier import (
    TrafficLightConfig,
    classify,
    load_config,
    save_config,
)

__all__ = ["TrafficLightConfig", "classify", "load_config", "save_config"]
