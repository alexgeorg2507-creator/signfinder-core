"""Структурированное логирование в stderr.

В Cloud Run / Cloud Logging stderr автоматически попадает в платформу.
Для on-prem можно перенаправить.
"""
from __future__ import annotations

import logging
import os
import sys

_CONFIGURED = False


def configure_logging(level: str | None = None) -> None:
    """Настраивает root logger один раз. Идемпотентно."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_level = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    numeric = getattr(logging, log_level, logging.INFO)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(numeric)
    # Не плодим хендлеры если кто-то уже подключил
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Получить именованный логгер. Автоматически конфигурирует root."""
    configure_logging()
    return logging.getLogger(name)
