"""UI-редактируемые статичные блоки промптов.

Хранятся в storage как `prompts.json`. Загружаются через get_runtime_prompts()
с fallback на DEFAULTS. Редактируются клиентом (Streamlit Settings page).

В v1.10 эти блоки будут per-provider (Anthropic / OpenAI / DeepSeek).
"""
from __future__ import annotations

from typing import Optional

from signfinder.storage.base import StorageBackend

_PROMPTS_FILE = "prompts.json"

DEFAULTS: dict[str, str] = {
    # validator.py
    "validator_rules": (
        "- Подпись в подвале каждой страницы (визирование) — реальное место\n"
        "- Строка вида «Клиент _________ ФИО» — реальное место\n"
        "- Упоминание «Клиент обязуется...» без подчёркиваний — НЕ место для подписи\n"
        "- Линия ___ + скобки с ФИО рядом — всегда реальное место\n"
        "- Слово «Подпись» без линии — НЕ место"
    ),
    # pattern_extractor.py + llm_finder.py — общая задача поиска
    "sign_task_rules": (
        "1. Найди строки/блоки с местами подписи стороны — НЕ упоминания в тексте.\n"
        "2. Признаки места подписи: подчёркивания (___), слово «Подпись», скобки с ФИО, роль + линия.\n"
        "3. Для каждого места составь regex-паттерн."
    ),
    "pattern_quality_rules": (
        "КРИТИЧЕСКИ ВАЖНО для паттернов:\n"
        "- Ты пишешь паттерны внутри JSON-строк — обратный слэш УДВАИВАТЬ\n"
        "- ПРАВИЛЬНО:  \"Арендатор[\\\\s_]*_{3,}\"\n"
        "- НЕПРАВИЛЬНО: \"Арендатор[\\s_]*_{3,}\"  (одинарный \\s сломает JSON)\n"
        "- ЗАПРЕЩЕНЫ жадные конструкции: НЕ пиши [\\\\s_]* — пиши [\\\\s_]{0,5}\n"
        "- ЗАПРЕЩЕНО .* без ограничения — пиши .{0,30}\n"
        "- Каждое место подписи = ОТДЕЛЬНЫЙ узкий паттерн, не один общий"
    ),
    "pattern_from_lines_rules": (
        "Составь regex-паттерны для поиска этих строк в тексте.\n\n"
        "КРИТИЧЕСКИ: паттерны внутри JSON-строк — обратный слэш УДВАИВАТЬ:\n"
        "- ПРАВИЛЬНО:  \"Арендатор[\\\\s_]*_{3,}\"\n"
        "- НЕПРАВИЛЬНО: \"Арендатор[\\s_]*_{3,}\"\n\n"
        "Верни ТОЛЬКО JSON-массив строк без markdown:\n"
        "[\"паттерн1\", \"паттерн2\"]"
    ),
    "pattern_narrow_strategies": (
        "Стратегии сужения паттернов:\n"
        "- Добавить уникальный контекст ПЕРЕД местом подписи (соседнее предложение, маркер блока)\n"
        "- Заменить жадные [\\\\s_]* на [\\\\s_]{0,5} или [\\\\s_]{1,10}\n"
        "- Использовать .{0,200} вместо .* для контекста\n"
        "- Добавить якоря начала строки ^ или конца $\n"
        "- Использовать lookahead/lookbehind для уникальной идентификации"
    ),
    # party_resolver.py
    "party_resolver_rules": (
        "- Подписант и компания могут указывать на разные стороны — приоритет AND-совпадение.\n"
        "- Если совпадение только по одному критерию — допустимо, но снизь confidence.\n"
        "- Если ни одного совпадения в тексте — confidence = 0, party = null.\n"
        "- Поле «party» должно ТОЧНО совпадать с одним из ключей в списке."
    ),
}

PROMPT_META: dict[str, dict[str, str]] = {
    "validator_rules": {
        "label": "Правила LLM-валидатора",
        "module": "llm/validator (pipeline)",
        "effect": "Что LLM считает реальным местом подписи vs ложным срабатыванием regex",
    },
    "sign_task_rules": {
        "label": "Задача поиска мест подписи",
        "module": "pipeline.pattern_extractor / pipeline.llm_finder",
        "effect": "Инструкция LLM — что именно искать и как",
    },
    "pattern_quality_rules": {
        "label": "Правила качества regex-паттернов",
        "module": "pipeline.pattern_extractor",
        "effect": "Ограничения на генерацию паттернов: экранирование, жадность, специфичность",
    },
    "pattern_from_lines_rules": {
        "label": "Инструкция паттернов из строк",
        "module": "pipeline.pattern_extractor",
        "effect": "Как LLM составляет паттерны когда места найдены вручную",
    },
    "pattern_narrow_strategies": {
        "label": "Стратегии сужения жадных паттернов",
        "module": "pipeline.pattern_extractor",
        "effect": "Что делать когда паттерны находят слишком много мест",
    },
    "party_resolver_rules": {
        "label": "Правила определения стороны по ФИО",
        "module": "pipeline.party_resolver",
        "effect": "Как LLM решает на какой стороне договора выступает подписант",
    },
}


def load_runtime_prompts(storage: Optional[StorageBackend]) -> dict[str, str]:
    """Загрузить runtime-промпты. Fallback на DEFAULTS.

    Если storage=None — возвращает DEFAULTS без чтения хранилища.
    """
    if storage is None:
        return dict(DEFAULTS)
    try:
        stored = storage.read_json(_PROMPTS_FILE)
        if not stored:
            return dict(DEFAULTS)
        merged = dict(DEFAULTS)
        merged.update(stored)
        return merged
    except Exception:
        return dict(DEFAULTS)


def save_runtime_prompts(storage: StorageBackend, prompts: dict[str, str]) -> None:
    """Сохранить runtime-промпты."""
    storage.write_json(_PROMPTS_FILE, prompts)


def get_prompt(storage: Optional[StorageBackend], key: str) -> str:
    """Получить один промпт-блок."""
    return load_runtime_prompts(storage).get(key, DEFAULTS.get(key, ""))
