"""DeepSeek LLM client — OpenAI-совместимый API с response_format JSON."""
from __future__ import annotations

import json
from typing import Any, Optional

from signfinder.llm.base import LLMClient, LLMError, _parse_json_response
from signfinder.llm.config import get_api_key
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


class DeepSeekClient(LLMClient):

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            try:
                import openai
            except ImportError as e:
                raise LLMError("openai SDK не установлен (нужен для DeepSeek): pip install openai") from e
            self._client = openai.OpenAI(
                api_key=get_api_key("deepseek"),
                base_url=BASE_URL,
            )
        return self._client

    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        model: Optional[str] = None,
        temperature: float = 0.0,
        reasoning: bool = True,
    ) -> str:
        client = self._ensure_client()
        # DeepSeek v4-family: dual Thinking/Non-Thinking mode, defaults to
        # enabled. See https://api-docs.deepseek.com/guides/thinking_mode/
        extra_body = None if reasoning else {"thinking": {"type": "disabled"}}
        try:
            resp = client.chat.completions.create(
                model=model or self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                messages=[{"role": "user", "content": prompt}],
                extra_body=extra_body,
            )
        except Exception as e:
            logger.error("DeepSeek API call failed: %s", e)
            raise LLMError(str(e)) from e
        choice = resp.choices[0]
        content = (choice.message.content or "").strip()
        if not content:
            reasoning = getattr(choice.message, "reasoning_content", None)
            logger.warning(
                "DeepSeek returned empty content (model=%s, finish_reason=%s, "
                "has_reasoning_content=%s, reasoning_len=%s, usage=%s)",
                model or self.model,
                getattr(choice, "finish_reason", None),
                reasoning is not None,
                len(reasoning) if reasoning else 0,
                getattr(resp, "usage", None),
            )
        return content

    def complete_structured(
        self,
        system: str,
        user: str,
        expected_json_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Native: response_format=json_object."""
        client = self._ensure_client()
        system_full = (
            f"{system}\n\n"
            "Respond ONLY with valid JSON matching this schema. "
            "No markdown, no explanation.\n"
            f"Schema: {json.dumps(expected_json_schema)}"
        )
        try:
            resp = client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {"role": "system", "content": system_full},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as e:
            raise LLMError(str(e)) from e
        text = (resp.choices[0].message.content or "").strip()
        return _parse_json_response(text, "deepseek")

    def is_available(self) -> bool:
        try:
            get_api_key("deepseek")
            import openai  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def provider_name(self) -> str:
        return "deepseek"
