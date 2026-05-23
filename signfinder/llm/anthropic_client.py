"""Реализация LLMClient через Anthropic Claude API."""
from __future__ import annotations

import os
from typing import Optional

from signfinder.llm.base import LLMClient, LLMError
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicClient(LLMClient):
    """LLMClient на базе Anthropic SDK.

    API key берётся из конструктора или из env ANTHROPIC_API_KEY.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        self.model = model
        # Lazy client — создаём при первом вызове, чтобы конструктор не падал
        # если ключа нет (для on-prem где LLM может быть отключён в принципе)
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self.api_key:
                raise LLMError("ANTHROPIC_API_KEY не задан")
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise LLMError("anthropic SDK не установлен") from e
            self._client = Anthropic(api_key=self.api_key)
        return self._client

    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        client = self._ensure_client()
        use_model = model or self.model
        try:
            resp = client.messages.create(
                model=use_model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.error("Anthropic API call failed: %s", e)
            raise LLMError(str(e)) from e

        if not resp.content:
            return ""
        # content[0] обычно TextBlock
        block = resp.content[0]
        return (getattr(block, "text", "") or "").strip()

    def is_available(self) -> bool:
        """True если API key есть и SDK импортируется."""
        if not self.api_key:
            return False
        try:
            import anthropic  # noqa: F401
            return True
        except ImportError:
            return False
