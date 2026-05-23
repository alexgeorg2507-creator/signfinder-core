"""Извлечение regex-паттернов мест подписи через LLM.

ПЕРЕНОС из core/pattern_extractor.py.
Изменения:
  - Использует LLMClient вместо прямого anthropic
  - Промпты вынесены в prompts/regex_generation.py
  - Runtime-промпт-блоки приходят явно
"""
from __future__ import annotations

import json
import re
import sys
from typing import Optional

from signfinder.llm.base import LLMClient, LLMError
from signfinder.prompts.regex_generation import (
    format_extract_patterns,
    format_narrow_patterns,
    format_regenerate_from_raw,
)


def extract_patterns(
    doc,
    party_name: str,
    language: str,
    llm: LLMClient,
    sign_task_rules: str,
    pattern_quality_rules: str,
    refinement: Optional[str] = None,
    previous_result: Optional[dict] = None,
) -> dict:
    """LLM анализирует документ → возвращает regex-паттерны.

    Возвращает dict: patterns, found_locations, reasoning, error, prompt, raw_response.
    """
    if not (hasattr(llm, "is_available") and llm.is_available()):
        return _error("LLM недоступен (API key не задан)")

    doc_text = _get_doc_text(doc, max_chars=5000)
    if not doc_text.strip():
        return _error("Не удалось извлечь текст из документа")

    try:
        llm_result = _call_extract(
            llm, doc_text, party_name, language,
            sign_task_rules, pattern_quality_rules,
            refinement, previous_result,
        )
        patterns = llm_result["patterns"]
        prompt_used = llm_result["prompt"]
        raw_response = llm_result["raw"]

        if not patterns:
            err = _error("LLM не вернул паттерны")
            err["prompt"] = prompt_used
            err["raw_response"] = raw_response
            return err

        return {
            "patterns": patterns,
            "found_locations": [],
            "reasoning": "",
            "error": None,
            "prompt": prompt_used,
            "raw_response": raw_response,
        }
    except (LLMError, Exception) as e:
        return _error(f"LLM error: {e}")


def merge_patterns_into_json(
    json_data: dict,
    party_name: str,
    language: str,
    new_patterns: list[str],
) -> int:
    """Добавляет паттерны в json_data in-place (без дублей).

    Возвращает количество реально добавленных паттернов.
    """
    parties = json_data.setdefault("parties", {})

    if party_name not in parties:
        parties[party_name] = {
            "display": party_name,
            "languages": {language: {"aliases": [], "patterns": []}},
            "notes": "",
        }

    party_block = parties[party_name]
    langs = party_block.setdefault("languages", {})

    if language not in langs:
        langs[language] = {"aliases": [], "patterns": []}

    existing = set(langs[language].get("patterns", []))
    added = 0
    for p in new_patterns:
        if p not in existing:
            langs[language]["patterns"].append(p)
            existing.add(p)
            added += 1

    return added


# ── Внутренние ───────────────────────────────────────────────────────────────

def _get_doc_text(doc, max_chars: int) -> str:
    buf = []
    total = 0
    for i, page in enumerate(doc.pages):
        text = page.text or ""
        header = f"\n--- Страница {i + 1} ---\n"
        chunk = header + text
        if total + len(chunk) >= max_chars:
            buf.append(chunk[: max_chars - total])
            break
        buf.append(chunk)
        total += len(chunk)
    return "\n".join(buf)


def _call_extract(
    llm: LLMClient,
    doc_text: str,
    party_name: str,
    language: str,
    sign_task_rules: str,
    pattern_quality_rules: str,
    refinement: Optional[str],
    previous_result: Optional[dict],
) -> dict:
    """Возвращает dict: {"patterns": list[str], "prompt": str, "raw": str}."""
    prompt = format_extract_patterns(
        doc_text=doc_text,
        party_name=party_name,
        language=language,
        sign_task_rules=sign_task_rules,
        pattern_quality_rules=pattern_quality_rules,
        refinement=refinement,
        previous_result=previous_result,
    )

    raw = ""
    try:
        raw = llm.complete(prompt, max_tokens=600)
        raw_clean = re.sub(r"^```(?:json)?", "", raw).strip()
        raw_clean = re.sub(r"```$", "", raw_clean).strip()
        result = json.loads(raw_clean)
        if isinstance(result, list):
            patterns = [p for p in result if isinstance(p, str)]
            return {"patterns": patterns, "prompt": prompt, "raw": raw}
    except Exception as e:
        sys.stderr.write(f"[pattern_extractor] _call_extract error: {e}, raw={raw[:200]}\n")
    return {"patterns": [], "prompt": prompt, "raw": raw}


def regenerate_from_raw_text(
    llm: LLMClient,
    party_name: str,
    locations: list[dict],
    doc,
    from_lines_rules: str,
) -> list[str]:
    """Перегенерирует паттерны по сырому тексту страниц.

    Применяется когда LLM-сгенерированные паттерны не находят ничего в реальном тексте.
    """
    page_indices = sorted({
        int(loc["page"]) - 1
        for loc in locations
        if isinstance(loc.get("page"), int) and loc["page"] > 0
    })
    if not page_indices:
        return []

    raw_blocks = []
    for idx in page_indices:
        if 0 <= idx < len(doc.pages):
            text = (doc.pages[idx].text or "")[:2500]
            raw_blocks.append(f"--- Страница {idx + 1} (сырой текст из PDF) ---\n{text}")
    raw_text = "\n\n".join(raw_blocks)

    if not raw_text.strip():
        return []

    prompt = format_regenerate_from_raw(party_name, locations, raw_text, from_lines_rules)

    try:
        raw = llm.complete(prompt, max_tokens=800)
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [p for p in result if isinstance(p, str)]
    except Exception as e:
        sys.stderr.write(f"[pattern_extractor] regenerate_from_raw_text error: {e}\n")
    return []


def patterns_match_anything(patterns: list[str], doc) -> bool:
    for p in patterns:
        try:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
        except re.error:
            continue
        for page in doc.pages:
            if rx.search(page.text or ""):
                return True
    return False


def count_matches(patterns: list[str], doc) -> int:
    total = 0
    for p in patterns:
        try:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
        except re.error:
            continue
        for page in doc.pages:
            total += len(rx.findall(page.text or ""))
    return total


def per_pattern_counts(patterns: list[str], doc) -> dict[str, int]:
    result = {}
    for p in patterns:
        try:
            rx = re.compile(p, re.IGNORECASE | re.UNICODE)
        except re.error:
            result[p] = -1
            continue
        cnt = 0
        for page in doc.pages:
            cnt += len(rx.findall(page.text or ""))
        result[p] = cnt
    return result


def narrow_patterns(
    llm: LLMClient,
    party_name: str,
    patterns: list[str],
    locations: list[dict],
    doc,
    actual_count: int,
    narrow_strategies: str,
) -> list[str]:
    """Сужает жадные паттерны через LLM."""
    by_pattern = per_pattern_counts(patterns, doc)

    page_indices = sorted({
        int(loc["page"]) - 1
        for loc in locations
        if isinstance(loc.get("page"), int) and loc["page"] > 0
    })
    raw_blocks = []
    for idx in page_indices:
        if 0 <= idx < len(doc.pages):
            text = (doc.pages[idx].text or "")[:2000]
            raw_blocks.append(f"--- Страница {idx + 1} ---\n{text}")
    raw_text = "\n\n".join(raw_blocks)

    prompt = format_narrow_patterns(
        party_name, patterns, locations, raw_text, actual_count, by_pattern, narrow_strategies,
    )

    try:
        raw = llm.complete(prompt, max_tokens=800)
        raw = re.sub(r"^```(?:json)?", "", raw).strip()
        raw = re.sub(r"```$", "", raw).strip()
        result = json.loads(raw)
        if isinstance(result, list):
            return [p for p in result if isinstance(p, str)]
    except Exception as e:
        sys.stderr.write(f"[pattern_extractor] narrow_patterns error: {e}\n")
    return []


def _error(msg: str) -> dict:
    return {
        "patterns": [],
        "found_locations": [],
        "reasoning": "",
        "error": msg,
    }
