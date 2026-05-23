"""Конфигурация SignFinder, читаемая из env vars."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

StorageMode = Literal["local", "gcs"]


@dataclass
class Config:
    """Конфигурация SignFinder.

    Читается из env vars методом from_env(), либо собирается явно в коде.
    """

    # Storage
    storage_mode: StorageMode = "local"
    storage_path: str = "./signfinder_data"
    gcs_bucket: str = "signfinder-config"

    # LLM
    anthropic_api_key: Optional[str] = None
    anthropic_model: str = "claude-sonnet-4-6"

    # Logging
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, **overrides) -> "Config":
        """Создаёт Config из env vars с возможностью override.

        Пример:
            cfg = Config.from_env(anthropic_api_key="sk-...")
        """
        mode_raw = os.environ.get("STORAGE_MODE", cls.storage_mode).lower()
        if mode_raw not in ("local", "gcs"):
            raise ValueError(
                f"STORAGE_MODE='{mode_raw}' invalid, expected 'local' or 'gcs'"
            )
        # Legacy: если задан GCS_BUCKET — режим gcs по умолчанию
        if "STORAGE_MODE" not in os.environ and os.environ.get("GCS_BUCKET"):
            mode_raw = "gcs"

        config = cls(
            storage_mode=mode_raw,  # type: ignore[arg-type]
            storage_path=os.environ.get("STORAGE_PATH", cls.storage_path),
            gcs_bucket=os.environ.get("GCS_BUCKET", cls.gcs_bucket),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            anthropic_model=os.environ.get("ANTHROPIC_MODEL", cls.anthropic_model),
            log_level=os.environ.get("LOG_LEVEL", cls.log_level),
        )
        for key, value in overrides.items():
            if hasattr(config, key):
                setattr(config, key, value)
            else:
                raise ValueError(f"Unknown Config field: {key}")
        return config

    def validate(self) -> None:
        """Проверка обязательных полей. Бросает ValueError при ошибках.

        Не вызывается автоматически — нужно вызывать явно если требуется
        строгая проверка (например, в Cloud API).
        """
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        if self.storage_mode == "gcs" and not self.gcs_bucket:
            raise ValueError("GCS_BUCKET is required for STORAGE_MODE=gcs")
        if self.storage_mode == "local" and not self.storage_path:
            raise ValueError("STORAGE_PATH is required for STORAGE_MODE=local")
