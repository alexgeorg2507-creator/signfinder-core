"""Google Gemini LLM client — response_mime_type для JSON."""
from __future__ import annotations

import json
from typing import Any, Optional

from signfinder.llm.base import LLMClient, LLMError, _parse_json_response
from signfinder.llm.config import get_api_key
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_MODEL = "gemini-1.5-flash"


class GeminiClient(LLMClient):

    def __init__(self, model: str = DEFAULT_MODEL):
        self.model = model
        self._genai = None
        self._model_obj = None

    def _ensure_client(self):
        if self._genai is None:
            try:
                import google.generativeai as genai
            except ImportError as e:
                raise LLMError(
                    "google-generativeai SDK не установлен: pip install google-generativeai"
                ) from e
            genai.configure(api_key=get_api_key("gemini"))
            self._genai = genai
            self._model_obj = genai.GenerativeModel(self.model)
        return self._model_obj

    def complete(
        self,
        prompt: str,
        max_tokens: int = 1000,
        model: Optional[str] = None,
        temperature: float = 0.0,
    ) -> str:
        model_obj = self._ensure_client()
        try:
            response = model_obj.generate_content(prompt)
            return (response.text or "").strip()
        except Exception as e:
            logger.error("Gemini API call failed: %s", e)
            raise LLMError(str(e)) from e

    def complete_structured(
        self,
        system: str,
        user: str,
        expected_json_schema: dict[str, Any],
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Native: response_mime_type=application/json."""
        model_obj = self._ensure_client()
        prompt = (
            f"{system}\n\n"
            "Respond ONLY with valid JSON matching this schema. "
            "No markdown, no explanation.\n"
            f"Schema: {json.dumps(expected_json_schema)}\n\n"
            f"{user}"
        )
        try:
            from google.generativeai.types import GenerationConfig
            response = model_obj.generate_content(
                prompt,
                generation_config=GenerationConfig(
                    response_mime_type="application/json",
                ),
            )
            text = (response.text or "").strip()
        except Exception as e:
            raise LLMError(str(e)) from e
        return _parse_json_response(text, "gemini")

    def is_available(self) -> bool:
        try:
            get_api_key("gemini")
            import google.generativeai  # noqa: F401
            return True
        except Exception:
            return False

    @property
    def provider_name(self) -> str:
        return "gemini"
