"""Абстрактный LLMClient — v1.10: поддержка мульти-провайдера."""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any, Optional


class LLMClient(ABC):
    """Базовый класс для LLM-провайдеров.

    Интерфейс:
      complete()            — старый, используется пайплайном (prompt: str)
      complete_structured() — новый, возвращает dict по JSON-схеме
    """

    @abstractmethod
    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        """Completion-запрос, возвращает текст ответа.

        Args:
            prompt: текст промпта (system + user в одном)
            max_tokens: лимит токенов
            model: переопределение модели
            temperature: temperature

        Returns:
            Текст ответа без markdown-обёртки.

        Raises:
            LLMError: при ошибке вызова API.
        """

    def complete_structured(
        self,
        system: str,
        user: str,
        expected_json_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """JSON-structured completion.

        Базовая реализация через complete() — провайдеры могут переопределить
        с нативными механизмами (function calling, response_format и т.п.).

        Returns:
            Parsed JSON dict.

        Raises:
            LLMError: при ошибке API или невалидном JSON.
        """
        prompt = (
            f"{system}\n\n"
            "Respond ONLY with valid JSON matching this schema. "
            "No markdown, no explanation.\n"
            f"Schema: {json.dumps(expected_json_schema)}\n\n"
            f"{user}"
        )
        raw = self.complete(prompt, max_tokens=max_tokens)
        return _parse_json_response(raw, self.provider_name)

    def is_available(self) -> bool:
        """True если провайдер настроен и готов к работе."""
        return True

    @property
    def provider_name(self) -> str:
        """Имя провайдера для логов и ошибок."""
        return self.__class__.__name__


class LLMError(Exception):
    """Ошибка вызова LLM API."""


def _parse_json_response(raw: str, provider: str) -> dict[str, Any]:
    """Парсит JSON из ответа LLM. Стрипает markdown-fences."""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
        raise LLMError(f"{provider} returned non-dict JSON: {type(result).__name__}")
    except json.JSONDecodeError as e:
        raise LLMError(
            f"{provider} returned invalid JSON: {e}\nRaw (first 300): {raw[:300]}"
        ) from e
