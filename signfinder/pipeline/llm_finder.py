"""LLM-first поиск мест подписи без regex (fallback).

ПЕРЕНОС из core/llm_finder.py.
Изменения:
  - LLMClient вместо прямого anthropic
  - sign_task_rules приходят явно
"""
from __future__ import annotations

import json
import re
import sys
from typing import Optional

try:
    import fitz
except ImportError:
    fitz = None  # type: ignore[assignment]

from signfinder.anchors.models import SignMatch
from signfinder.llm.base import LLMClient, LLMError


def find_signatures_llm(
    doc,
    party_name: str,
    language: str,
    llm: LLMClient,
    sign_task_rules: str,
) -> list[SignMatch]:
    """LLM находит места подписи напрямую, без regex.

    Используется как fallback когда regex находит 0 мест.
    """
    if not (hasattr(llm, "is_available") and llm.is_available()):
        return []

    doc_text = _get_strategic_text(doc, max_chars=10000)
    if not doc_text.strip():
        return []

    try:
        locations = _call_llm(llm, doc_text, party_name, language, sign_task_rules)
        if not locations:
            return []

        matches = []
        counter = 0
        pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")

        try:
            for loc in locations:
                page_idx = int(loc.get("page", 1)) - 1
                if page_idx < 0 or page_idx >= len(doc.pages):
                    continue

                text_marker = loc.get("text_marker", "")
                if not text_marker:
                    continue

                page = pdf_doc[page_idx]
                rects = _find_marker_bbox(page, text_marker)

                for rect in rects:
                    counter += 1
                    ctx = loc.get("context", text_marker)[:100]
                    matches.append(SignMatch(
                        id=f"llm_{counter:03d}",
                        page=page_idx,
                        bbox=tuple(rect),
                        context=ctx,
                        party=party_name,
                        pattern="llm_fallback",
                        confidence=0.7,
                    ))
        finally:
            pdf_doc.close()

        return matches

    except (LLMError, Exception) as e:
        sys.stderr.write(f"[llm_finder] error: {e}\n")
        return []


def _get_strategic_text(doc, max_chars: int) -> str:
    SIGN_MARKERS = ["подпись", "signature", "podpis", "___", "ФИО", "печать"]
    pages = doc.pages
    n = len(pages)

    priority = [0]
    if n > 1:
        priority.append(n - 1)
    for i, page in enumerate(pages):
        if i in priority:
            continue
        text = (page.text or "").lower()
        if any(m in text for m in SIGN_MARKERS):
            priority.append(i)
    rest = [i for i in range(n) if i not in priority]

    buf = []
    total = 0
    for i in (priority + rest):
        chunk = f"\n--- Страница {i + 1} ---\n{pages[i].text or ''}"
        if total + len(chunk) >= max_chars:
            buf.append(chunk[: max_chars - total])
            break
        buf.append(chunk)
        total += len(chunk)
    return "\n".join(buf)


def _call_llm(
    llm: LLMClient,
    doc_text: str,
    party_name: str,
    language: str,
    sign_task_rules: str,
) -> list[dict]:
    lang_hint = {"ru": "русском", "en": "английском", "pl": "польском"}.get(language, language)

    prompt = (
        f"Анализируй договор на {lang_hint} языке. "
        f"Найди все места где ПОДПИСЫВАЕТ сторона \"{party_name}\".\n\n"
        f"Договор (фрагмент):\n---\n{doc_text}\n---\n\n"
        f"{sign_task_rules}\n"
        "3. Для каждого места укажи: номер страницы, точный фрагмент текста (text_marker), контекст вокруг.\n\n"
        "ВАЖНО:\n"
        "- text_marker должен быть ТОЧНОЙ строкой из документа (10-30 символов), включая подчёркивания.\n"
        "- НЕ обобщай — копируй точный текст как он есть в документе.\n"
        "- Контекст — 2-3 слова до и после для уникальной идентификации.\n\n"
        "Верни ТОЛЬКО JSON-массив без markdown:\n"
        "[\n"
        '  {"page": 1, "text_marker": "Подпись_____________", "context": "Директор: Подпись_____________ /ФИО/"},\n'
        "  ...\n"
        "]"
    )

    raw = llm.complete(prompt, max_tokens=1500)
    raw = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
    raw = re.sub(r"```$", "", raw, flags=re.MULTILINE).strip()

    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if m:
        raw = m.group(0)

    try:
        locations = json.loads(raw)
        if isinstance(locations, list):
            return locations
    except json.JSONDecodeError:
        pass

    return []


def _find_marker_bbox(page, text_marker: str) -> list:
    rects = page.search_for(text_marker)
    if rects:
        return rects

    cleaned = re.sub(r"_{2,}", " ", text_marker)
    words = re.findall(r"[\w\u0400-\u04FF]+", cleaned, flags=re.UNICODE)
    if words:
        for w in words:
            if len(w) >= 3:
                found = page.search_for(w)
                if found:
                    return found[:1]

    if "___" in text_marker or "__" in text_marker:
        lines = page.search_for("___")
        return lines[:1] if lines else []

    return []
