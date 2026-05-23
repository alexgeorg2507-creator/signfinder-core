"""Абстрактный класс LLMClient — заготовка для v1.10 (мульти-провайдер)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class LLMClient(ABC):
    """Базовый класс для LLM-провайдеров.

    В v1.9 единственная реализация — AnthropicClient.
    В v1.10 добавятся OpenAIClient, DeepSeekClient — пайплайн не меняется,
    меняется только провайдер.
    """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        """Делает completion-запрос и возвращает текст ответа.

        Args:
            prompt: текст промпта
            max_tokens: лимит токенов в ответе
            model: переопределение модели (если None — берётся из конструктора)
            temperature: temperature (по умолчанию 0 для предсказуемости)

        Returns:
            Текст ответа без markdown-обёртки.

        Raises:
            LLMError: при ошибке вызова API.
        """
        ...


class LLMError(Exception):
    """Ошибка вызова LLM API."""
