"""Определение языка документа.

Стратегия:
  1. Если parser уже определил язык (doc.language) и это известный код — используем.
  2. Иначе — LLM fallback по первым ~2000 символам через переданный LLMClient.
  3. Если LLM недоступен или вернул мусор — возвращаем 'unknown'.

Поддерживаемые языки: ru, en, pl. Остальные → 'unknown'.
"""
from __future__ import annotations

from typing import Optional

from signfinder.llm.base import LLMClient, LLMError

SUPPORTED = ("ru", "en", "pl")


def detect_language(doc, llm: Optional[LLMClient] = None) -> str:
    """Возвращает 'ru' / 'en' / 'pl' / 'unknown'.

    Параметры:
        doc — ParsedDocument с полями .language (опц.) и .pages[].text.
        llm — LLMClient для fallback. Если None — fallback пропускается.
    """
    parser_lang = (getattr(doc, "language", "") or "").lower()[:2]
    if parser_lang in SUPPORTED:
        return parser_lang

    if llm is None:
        return "unknown"

    try:
        sample = _get_sample(doc, max_chars=2000)
        if not sample.strip():
            return "unknown"
        return _llm_detect(llm, sample)
    except Exception:
        return "unknown"


def _get_sample(doc, max_chars: int) -> str:
    buf = []
    total = 0
    for page in doc.pages:
        text = page.text or ""
        if total + len(text) >= max_chars:
            buf.append(text[: max_chars - total])
            break
        buf.append(text)
        total += len(text)
    return "\n".join(buf)


def _llm_detect(llm: LLMClient, sample: str) -> str:
    prompt = (
        "Определи язык фрагмента договора. Ответь ОДНИМ кодом из списка: "
        "ru, en, pl, unknown. Никаких пояснений, только код.\n\n"
        f"Фрагмент:\n{sample}"
    )
    try:
        answer = llm.complete(prompt, max_tokens=10).strip().lower()
    except LLMError:
        return "unknown"
    if answer in SUPPORTED:
        return answer
    return "unknown"
