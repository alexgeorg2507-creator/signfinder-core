"""Промпт: определение нашей стороны в шапке договора (step 3 пайплайна)."""
from __future__ import annotations

import json as _json


_PROMPT_FIND_OUR_SIDE = """Ты — юридический аналитик. Анализируй ШАПКУ договора и найди НАШУ СТОРОНУ.

Шапка договора:
{header_text}

Язык договора: {language}

Наши данные:
- Компания (алиасы): {company_aliases}
- Подписант (алиасы): {signer_aliases}

Универсальные маркеры подписи: {markers}

Задача:
1. Найди ВСЕ стороны договора в шапке (обычно две стороны)
2. Для каждой стороны определи: юрлицо, роль (Арендатор, Исполнитель, Заказчик и т.п.), ФИО подписанта
3. Определи КАКАЯ из сторон — НАША по совпадению с нашими алиасами (компания ИЛИ ФИО, фамилия первичнее)
4. Верни синонимы НАШЕЙ стороны как они написаны в этом договоре

Правила:
- Если найдено несколько потенциальных совпадений — снизь confidence
- Фамилия подписанта важнее имени и инициалов
- Если ни одного совпадения — our_side_index = null

Верни ТОЛЬКО JSON без markdown:
{{
  "all_parties": [
    {{"legal_entity": "...", "role": "...", "signer": "..."}},
    {{"legal_entity": "...", "role": "...", "signer": "..."}}
  ],
  "our_side_index": 0,
  "our_side_synonyms": {{
    "legal_entity": "...",
    "roles": ["...", "..."],
    "signer": "..."
  }},
  "confidence": 0.9,
  "match_reason": "company match | signer match | both | none",
  "evidence": "цитата из шапки где упомянута наша сторона"
}}"""


def format_find_our_side(
    header_text: str,
    language: str,
    company_aliases: list,
    signer_aliases: list,
    markers: dict,
) -> str:
    """Сформировать промпт определения нашей стороны."""
    return _PROMPT_FIND_OUR_SIDE.format(
        header_text=header_text,
        language=language,
        company_aliases=", ".join(company_aliases) if company_aliases else "(не указано)",
        signer_aliases=", ".join(signer_aliases) if signer_aliases else "(не указано)",
        markers=_json.dumps(markers, ensure_ascii=False),
    )
