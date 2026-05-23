"""Промпт: определение стороны по ФИО и компании (party_resolver)."""
from __future__ import annotations


def format_party_resolver(
    doc_text: str,
    signer_name: str,
    company: str | None,
    parties_str: str,
    resolver_rules: str,
) -> str:
    """Промпт для party_resolver.resolve_party()."""
    company_line = f'Компания: "{company}"' if company else "Компания: (не указана)"
    return (
        f"Фрагмент договора (первые символы):\n---\n{doc_text}\n---\n\n"
        f"Подписант: \"{signer_name}\"\n"
        f"{company_line}\n\n"
        f"Доступные стороны договора (выбери ОДНУ из этого списка):\n{parties_str}\n\n"
        "Задача: определи, на какой стороне договора выступает указанный подписант или компания.\n\n"
        f"Правила:\n{resolver_rules}\n\n"
        "Верни ТОЛЬКО JSON без обрамления markdown, без пояснений:\n"
        '{"party": "<точное имя из списка или null>", "confidence": <число 0..1>, "evidence": "<цитата из договора, до 200 символов>"}'
    )
