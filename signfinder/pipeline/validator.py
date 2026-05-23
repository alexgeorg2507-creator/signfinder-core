"""LLM-валидация найденных мест подписи.

ПЕРЕНОС из core/validator.py.
Изменения:
  - Удалён прямой импорт anthropic — используется LLMClient
  - validator_rules приходят явно
"""
from __future__ import annotations

import json
import re
import sys
from typing import Optional

from signfinder.anchors.models import SignMatch
from signfinder.llm.base import LLMClient, LLMError


def validate_with_llm(
    matches: list[SignMatch],
    party_name: str,
    llm: Optional[LLMClient],
    validator_rules: str,
) -> list[SignMatch]:
    """Прогон через LLM — определить реальные места подписи и шум.

    Если llm=None или LLM недоступен — возвращает matches с confidence=0.5.
    При ошибке API — graceful degradation, confidence=0.5.
    """
    if not matches:
        return matches

    if llm is None or (hasattr(llm, "is_available") and not llm.is_available()):
        for m in matches:
            m.confidence = 0.5
        return matches

    from signfinder.prompts.validation import format_validator

    items = [
        {"id": m.id, "page": m.page + 1, "context": m.context, "pattern": m.pattern}
        for m in matches
    ]

    prompt = format_validator(items, party_name, validator_rules)

    try:
        text = llm.complete(prompt, max_tokens=2000)
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            text = m.group(0)
        validations = json.loads(text)
        val_map = {v["id"]: v for v in validations}

        for match in matches:
            v = val_map.get(match.id)
            if v:
                match.confidence = float(v.get("confidence", 0.5))
                if not v.get("is_signature", True):
                    match.status = "rejected_by_llm"
                    match.correction_applied = "LLM"
            else:
                match.confidence = 0.5
    except (LLMError, Exception) as e:
        for match in matches:
            match.confidence = 0.5
        sys.stderr.write(f"[validator] LLM error: {e}\n")

    return matches
