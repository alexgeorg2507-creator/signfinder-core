"""OpenAI LLM client — complete() + complete_structured() через function calling."""
from __future__ import annotations

import json
from typing import Any, Optional

from signfinder.llm.base import LLMClient, LLMError, _parse_json_response
from signfinder.llm.config import get_api_key
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "gpt-4o"


class OpenAIClient(LLMClient):

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise LLMError("openai SDK не установлен: pip install openai") from e
            self._client = openai.OpenAI(api_key=get_api_key("openai"))
        return self._client

    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        client = self._ensure_client()
        try:
            resp = client.chat.completions.create(
                model=model or self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
            )
        except Exception as e:
            logger.error("OpenAI API call failed: %s", e)
            raise LLMError(str(e)) from e
        return (resp.choices[0].message.content or "").strip()

    def complete_structured(
        self,
        system: str,
        user: str,
        expected_json_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Native: function calling для гарантированного JSON."""
        client = self._ensure_client()
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                tools=[{
                    "type": "function",
                    "function": {
                        "name": "respond",
                        "description": "Structured JSON response",
                        "parameters": expected_json_schema,
                    },
                }],
                tool_choice={"type": "function", "function": {"name": "respond"}},
            )
        except Exception as e:
            raise LLMError(str(e)) from e
        msg = resp.choices[0].message
        if msg.tool_calls:
            raw = msg.tool_calls[0].function.arguments
            return _parse_json_response(raw, "openai")
        raise LLMError("OpenAI не вернул function call несмотря на tool_choice")

    def is_available(self) -> bool:
        try:
            get_api_key("openai")
            import openai  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def provider_name(self) -> str:
        return "openai"
