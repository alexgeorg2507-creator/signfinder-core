"""LLM client factory — создаёт нужный клиент по провайдеру."""
from __future__ import annotations

import importlib

from signfinder.llm.base import LLMClient
from signfinder.llm.config import get_active_provider, get_api_key, SUPPORTED_PROVIDERS

_CLASS_MAP: dict[str, str] = {
    "anthropic": "signfinder.llm.anthropic_client.AnthropicClient",
    "openai":    "signfinder.llm.openai_client.OpenAIClient",
    "deepseek":  "signfinder.llm.deepseek_client.DeepSeekClient",
    "gemini":    "signfinder.llm.gemini_client.GeminiClient",
}


def create_client(provider: str | None = None) -> LLMClient:
    """Создаёт LLMClient для указанного провайдера.

    Args:
        provider: имя провайдера. None → читается из конфига.

    Returns:
        Инициализированный LLMClient.

    Raises:
        RuntimeError: неизвестный провайдер или не настроен API key.
    """
    if provider is None:
        provider = get_active_provider()

    provider = provider.lower().strip()
    if provider not in _CLASS_MAP:
        raise RuntimeError(
            f"Неизвестный LLM провайдер: '{provider}'. "
            f"Поддерживаются: {SUPPORTED_PROVIDERS}"
        )

    # Быстрая проверка ключа до импорта SDK
    get_api_key(provider)

    # Lazy import — не грузим все SDK при старте
    module_path, class_name = _CLASS_MAP[provider].rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)
    return cls()


def available_providers() -> list[str]:
    """Все поддерживаемые провайдеры."""
    return list(SUPPORTED_PROVIDERS)
