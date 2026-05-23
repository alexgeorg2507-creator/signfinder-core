"""Промпты: генерация regex-паттернов мест подписи (step 4 + pattern_extractor)."""
from __future__ import annotations

import json as _json
from typing import Optional


_PROMPT_GENERATE_REGEX = """Ты — инженер по регулярным выражениям. Создай regex-паттерны для поиска мест подписи.

Сторона, для которой ищем места подписи:
- Юрлицо: {legal_entity}
- Роли: {roles}
- Подписант: {signer}

Маркеры подписи для языка {language}:
- Подчёркивания (паттерны): {underline_patterns}
- Слова-маркеры: {marker_words}

Стратегические фрагменты документа:
{strategic_fragments}

Правила:
1. Паттерны должны ловить СТРУКТУРУ "синоним стороны + маркер места подписи", НЕ конкретные ФИО
2. Используй ВСЕ типы синонимов (роли, юрлицо, подписант — каждый отдельно)
3. Используй маркеры из переданного списка
4. НЕ создавай паттерны совпадающие со второй стороной
5. Приоритетные зоны: футер страниц, конец разделов, конец договора, приложения
6. Если в фрагментах виден явный паттерн (например "{role} _____") — обязательно включи

КРИТИЧЕСКИ — паттерны внутри JSON-строк: обратный слэш УДВАИВАТЬ
- ПРАВИЛЬНО:  "Арендатор[\\\\s_]{0,5}_{3,}"
- НЕПРАВИЛЬНО: "Арендатор[\\s_]*_{3,}"
- ЗАПРЕЩЕНЫ жадные .* — только .{0,50}

Верни ТОЛЬКО JSON без markdown:
{
  "patterns": [
    {"pattern": "...", "reason": "ловит подпись в футере страницы"},
    {"pattern": "...", "reason": "ловит подпись в конце раздела"}
  ]
}"""


def format_generate_regex(
    legal_entity: str,
    roles: list,
    signer: str,
    language: str,
    markers_block: dict,
    strategic_fragments: str,
) -> str:
    """Сформировать промпт генерации regex.

    Используем str.replace, а не .format() — в промпте присутствуют {} в
    примерах regex, которые format() сломает.
    """
    substitutions = {
        "{legal_entity}": legal_entity or "(не определено)",
        "{roles}": ", ".join(roles) if roles else "(не определено)",
        "{signer}": signer or "(не определено)",
        "{language}": language,
        "{underline_patterns}": ", ".join(markers_block.get("underline_patterns", [])),
        "{marker_words}": ", ".join(markers_block.get("marker_words", [])),
        "{strategic_fragments}": strategic_fragments,
    }
    result = _PROMPT_GENERATE_REGEX
    for placeholder, value in substitutions.items():
        result = result.replace(placeholder, value)
    return result


# ── pattern_extractor: первичная генерация по тексту документа ────────────────

def format_extract_patterns(
    doc_text: str,
    party_name: str,
    language: str,
    sign_task_rules: str,
    pattern_quality_rules: str,
    refinement: Optional[str] = None,
    previous_result: Optional[dict] = None,
) -> str:
    """Промпт для pattern_extractor.extract_patterns().

    Принимает runtime-промпт-блоки явно (sign_task_rules, pattern_quality_rules) —
    они UI-редактируемые и грузятся через prompts.settings.
    """
    refinement_block = ""
    if refinement and previous_result:
        refinement_block = (
            "\nПредыдущий анализ:\n"
            f"Паттерны: {_json.dumps(previous_result.get('patterns', []), ensure_ascii=False)}\n"
            f"Места: {_json.dumps(previous_result.get('found_locations', []), ensure_ascii=False)}\n\n"
            f"Уточнение оператора: \"{refinement}\"\n"
            "Скорректируй результат с учётом уточнения. ОБЯЗАТЕЛЬНО пересмотри паттерны под новые места — старые паттерны могут быть неактуальны.\n"
        )
    elif refinement:
        refinement_block = (
            f"\nДополнительные инструкции оператора: \"{refinement}\"\n"
            "Учти их при анализе документа.\n"
        )

    lang_hint = {"ru": "русском", "en": "английском", "pl": "польском"}.get(
        language, language
    )

    return (
        f"Анализируй договор на {lang_hint} языке. "
        f"Найди все места где ПОДПИСЫВАЕТ сторона \"{party_name}\".\n\n"
        f"Договор (фрагмент):\n---\n{doc_text}\n---\n"
        f"{refinement_block}\n"
        f"{sign_task_rules}\n\n"
        f"{pattern_quality_rules}\n\n"
        "Верни ТОЛЬКО JSON-массив строк без markdown:\n"
        "[\"паттерн1\", \"паттерн2\"]"
    )


# ── pattern_extractor: регенерация паттернов по сырому тексту ─────────────────

def format_regenerate_from_raw(
    party_name: str,
    locations: list[dict],
    raw_text: str,
    from_lines_rules: str,
) -> str:
    locations_str = _json.dumps(locations[:5], ensure_ascii=False, indent=2)
    return (
        "ВАЖНО: предыдущие сгенерированные паттерны не нашли ни одного места в реальном тексте документа.\n"
        "Нужно создать паттерны на основе ТОЧНОГО сырого текста как он извлекается из PDF.\n\n"
        f"Места которые нужно найти (по описанию):\n{locations_str}\n\n"
        f"РЕАЛЬНЫЙ СЫРОЙ ТЕКСТ страниц где должны быть эти места (как выдаёт fitz):\n{raw_text}\n\n"
        "Внимательно изучи РЕАЛЬНУЮ структуру текста:\n"
        f"- Между словом \"{party_name}\" и линией могут быть: пробелы, табы (\\t), переносы строк (\\n), двоеточия, скобки\n"
        "- Линия может быть НЕ подчёркиваниями — а пробелами, точками, дефисами\n"
        "- Слова могут быть разделены неожиданными способами\n"
        "- Иногда роль и линия вообще на разных строках\n\n"
        f"{from_lines_rules}\n\n"
        "КРИТИЧЕСКИ для JSON: \\s, \\S, \\d, \\w, \\n — с ДВОЙНЫМ слэшем.\n"
        "Без якорей ^ и $. Применяется флаг IGNORECASE | UNICODE.\n\n"
        "Верни ТОЛЬКО JSON-массив паттернов, без markdown:\n"
        "[\"паттерн1\", \"паттерн2\"]"
    )


# ── pattern_extractor: сужение жадных паттернов ───────────────────────────────

def format_narrow_patterns(
    party_name: str,
    patterns: list[str],
    locations: list[dict],
    raw_text: str,
    actual_count: int,
    per_pattern_counts: dict[str, int],
    narrow_strategies: str,
) -> str:
    expected = len(locations)
    counts_str = "\n".join(f'  - "{p}" → {c} матчей' for p, c in per_pattern_counts.items())
    locations_str = _json.dumps(locations[:5], ensure_ascii=False, indent=2)
    return (
        "ПРОБЛЕМА: паттерны слишком жадные.\n"
        f"Ожидалось мест подписи стороны \"{party_name}\": {expected}\n"
        f"Фактически находят: {actual_count} мест ({actual_count - expected} лишних)\n\n"
        f"Диагностика по каждому паттерну:\n{counts_str}\n\n"
        f"Места которые ДОЛЖНЫ быть найдены (целевые):\n{locations_str}\n\n"
        f"Сырой текст страниц:\n{raw_text}\n\n"
        f"Задача: переписать паттерны так чтобы они находили РОВНО {expected} мест.\n\n"
        f"{narrow_strategies}\n\n"
        "КРИТИЧЕСКИ для JSON: \\s, \\S, \\d, \\w, \\n — с ДВОЙНЫМ слэшем.\n\n"
        "Верни ТОЛЬКО JSON-массив без markdown:\n"
        "[\"паттерн1\", \"паттерн2\"]"
    )
