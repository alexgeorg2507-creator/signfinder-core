"""Промпт: LLM-валидация найденных мест подписи."""
from __future__ import annotations

import json as _json


def format_validator(items: list[dict], party_name: str, validator_rules: str) -> str:
    """Промпт для validator.validate_with_llm().

    items — список {id, page, context, pattern}.
    validator_rules — UI-редактируемый блок правил из prompts.settings.
    """
    return (
        f"Ты валидируешь места для подписи стороны \"{party_name}\" в договоре.\n"
        "Для каждого найденного места определи: это реальное место для подписи или ложное "
        "срабатывание (например, упоминание роли в тексте без линии для подписи)?\n\n"
        f"Учти распространённые конвенции:\n{validator_rules}\n\n"
        f"Найденные места:\n{_json.dumps(items, ensure_ascii=False, indent=2)}\n\n"
        "Ответь ТОЛЬКО JSON-массивом, без пояснений и markdown:\n"
        '[{"id":"sig_001","is_signature":true,"confidence":0.95,"reason":"короткое пояснение"},...]'
    )
