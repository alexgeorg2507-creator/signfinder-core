"""LLM-промпты SignFinder.

- settings.py — UI-редактируемые блоки (validator_rules, sign_task_rules и т.п.)
- extraction.py — определение нашей стороны
- regex_generation.py — генерация regex-паттернов
- validation.py — валидация найденных мест
- party_resolver.py — определение стороны по ФИО
"""
from signfinder.prompts.extraction import format_find_our_side
from signfinder.prompts.party_resolver import format_party_resolver
from signfinder.prompts.regex_generation import (
    format_extract_patterns,
    format_generate_regex,
    format_narrow_patterns,
    format_regenerate_from_raw,
)
from signfinder.prompts.settings import (
    DEFAULTS,
    PROMPT_META,
    get_prompt,
    load_runtime_prompts,
    save_runtime_prompts,
)
from signfinder.prompts.validation import format_validator

__all__ = [
    "DEFAULTS",
    "PROMPT_META",
    "load_runtime_prompts",
    "save_runtime_prompts",
    "get_prompt",
    "format_find_our_side",
    "format_generate_regex",
    "format_extract_patterns",
    "format_regenerate_from_raw",
    "format_narrow_patterns",
    "format_validator",
    "format_party_resolver",
]
