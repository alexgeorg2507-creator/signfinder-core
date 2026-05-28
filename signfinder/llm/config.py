"""Чтение/запись llm_config.json + fallback на env vars."""
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

_ENV_KEY_MAP: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai":    "OPENAI_API_KEY",
    "deepseek":  "DEEPSEEK_API_KEY",
    "gemini":    "GEMINI_API_KEY",
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
    """
    Приоритет: llm_config.json → env LLM_PROVIDER → RuntimeError.
    Провайдер должен иметь непустой api_key.
    """
    config = load_config()
    provider = config.get("active_provider", "").strip().lower()
    if provider and _get_key_from_config(config, provider):
        return provider

    # fallback: env
    env_provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    if env_provider and os.environ.get(_ENV_KEY_MAP.get(env_provider, ""), "").strip():
        return env_provider

    raise RuntimeError(
        "LLM не настроен. Задай active_provider в llm_config.json "
        "или переменные LLM_PROVIDER + <PROVIDER>_API_KEY."
    )


def get_api_key(provider: str) -> str:
    """
    Приоритет: llm_config.json → env var → RuntimeError.
    """
    config = load_config()
    key = _get_key_from_config(config, provider)
    if key:
        return key
    env_key = os.environ.get(_ENV_KEY_MAP.get(provider, ""), "").strip()
    if env_key:
        return env_key
    raise RuntimeError(f"API key не настроен для провайдера: {provider}")


def configured_providers(config: dict[str, Any] | None = None) -> list[str]:
    """Провайдеры с непустым api_key."""
    if config is None:
        config = load_config()
    result = []
    for p in SUPPORTED_PROVIDERS:
        if _get_key_from_config(config, p):
            result.append(p)
    # also check env vars for providers not in config
    for p in SUPPORTED_PROVIDERS:
        if p not in result and os.environ.get(_ENV_KEY_MAP.get(p, ""), "").strip():
            result.append(p)
    return result


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
