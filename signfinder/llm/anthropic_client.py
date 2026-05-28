"""Anthropic Claude LLM client — v1.10 (обновлён)."""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from signfinder.llm.base import LLMClient, LLMError, _parse_json_response
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "claude-sonnet-4-6"


class AnthropicClient(LLMClient):
    """LLMClient на базе Anthropic SDK.

    API key берётся из:
      1. конструктор api_key=
      2. llm_config.json (через signfinder.llm.config)
      3. env ANTHROPIC_API_KEY
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
    ):
        self._explicit_key = api_key
        self.model = model
        self._client = None

    def _get_key(self) -> str:
        if self._explicit_key:
            return self._explicit_key
        # Try config/env
        try:
            from signfinder.llm.config import get_api_key
            return get_api_key("anthropic")
        except RuntimeError:
            pass
        raise LLMError("ANTHROPIC_API_KEY не задан")

    def _ensure_client(self):
        if self._client is None:
            key = self._get_key()
            try:
                from anthropic import Anthropic
            except ImportError as e:
                raise LLMError("anthropic SDK не установлен") from e
            self._client = Anthropic(api_key=key)
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
        block = resp.content[0]
        return (getattr(block, "text", "") or "").strip()

    def complete_structured(
        self,
        system: str,
        user: str,
        expected_json_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Native Anthropic: system + user раздельно."""
        client = self._ensure_client()
        system_full = (
            f"{system}\n\n"
            "Respond ONLY with valid JSON matching this schema. "
            "No markdown, no explanation.\n"
            f"Schema: {json.dumps(expected_json_schema)}"
        )
        try:
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system_full,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as e:
            raise LLMError(str(e)) from e
        text = (getattr(resp.content[0], "text", "") or "").strip()
        return _parse_json_response(text, "anthropic")

    def is_available(self) -> bool:
        try:
            self._get_key()
            import anthropic  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def provider_name(self) -> str:
        return "anthropic"
