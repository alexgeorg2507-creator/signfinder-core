"""Чтение/запись llm_config.json.

LLM-провайдер и ключи настраиваются ТОЛЬКО через интерфейс (Настройки → LLM),
которые сохраняются в llm_config.json. Env vars для LLM не используются —
это исключает случайное использование старых ключей из .env.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "active_provider": "",
    "providers": {
        "anthropic": {"api_key": ""},
        "openai":    {"api_key": ""},
        "deepseek":  {"api_key": ""},
        "gemini":    {"api_key": ""},
    },
}

SUPPORTED_PROVIDERS: list[str] = list(DEFAULT_CONFIG["providers"].keys())


def _config_path() -> Path:
    env = os.environ.get("LLM_CONFIG_PATH", "").strip()
    if env:
        return Path(env)
    return Path("/data/api/llm_config.json")


def load_config() -> dict[str, Any]:
    """Загружает конфиг из JSON-файла. Отсутствующие ключи заполняются дефолтами."""
    path = _config_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for provider in SUPPORTED_PROVIDERS:
                data.setdefault("providers", {})
                data["providers"].setdefault(provider, {"api_key": ""})
            return data
        except (json.JSONDecodeError, OSError):
            pass
    import copy
    return copy.deepcopy(DEFAULT_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    """Сохраняет конфиг в JSON-файл."""
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_active_provider() -> str:
    """Читает активный провайдер из llm_config.json.

    Env vars НЕ используются — провайдер настраивается только через UI.
    Если провайдер не настроен — понятная ошибка с инструкцией.
    """
    config = load_config()
    provider = config.get("active_provider", "").strip().lower()
    if provider and _get_key_from_config(config, provider):
        return provider

    raise RuntimeError(
        "LLM-провайдер не настроен. "
        "Откройте Настройки → LLM, выберите провайдера и введите API-ключ."
    )


def get_api_key(provider: str) -> str:
    """Читает API-ключ из llm_config.json.

    Env vars НЕ используются — ключи настраиваются только через UI.
    """
    config = load_config()
    key = _get_key_from_config(config, provider)
    if key:
        return key
    raise RuntimeError(
        f"API-ключ не настроен для провайдера '{provider}'. "
        f"Откройте Настройки → LLM и введите ключ."
    )


def configured_providers(config: dict[str, Any] | None = None) -> list[str]:
    """Провайдеры с непустым api_key в llm_config.json."""
    if config is None:
        config = load_config()
    return [
        p for p in SUPPORTED_PROVIDERS
        if _get_key_from_config(config, p)
    ]


def mask_key(key: str) -> str:
    """sk-ant-api03-abcXXX... → sk-ant-api03-***"""
    if not key:
        return ""
    parts = key.split("-")
    visible = "-".join(parts[:3]) if len(parts) >= 3 else key[:8]
    return f"{visible}-***"


def _get_key_from_config(config: dict[str, Any], provider: str) -> str:
    return (
        config.get("providers", {})
        .get(provider, {})
        .get("api_key", "")
        .strip()
    )
