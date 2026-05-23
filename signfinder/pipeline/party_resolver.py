"""Идентификация стороны договора по ФИО и/или компании.

ПЕРЕНОС из core/party_resolver.py.
Изменения:
  - Удалён прямой импорт anthropic — используется LLMClient
  - Промпт вынесен в prompts/party_resolver.py
  - Промпт-правила приходят явно (resolver_rules: str)
"""
from __future__ import annotations

import json
import re
from typing import Optional

from signfinder.llm.base import LLMClient, LLMError


def resolve_party(
    doc,
    signer_name: str,
    company: Optional[str],
    parties: list[dict],
    llm: LLMClient,
    resolver_rules: str,
) -> dict:
    """Определить сторону договора.

    Параметры:
        doc — ParsedDocument
        signer_name — обязательное ФИО подписанта
        company — опциональное название компании
        parties — список сторон из parse_parties_json()
        llm — LLMClient
        resolver_rules — runtime-промпт-блок из prompts.settings

    Возвращает dict:
        {"party": <name|None>, "confidence": 0..1, "evidence": "...", "error": <str|None>}
    """
    if not signer_name or not signer_name.strip():
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "ФИО подписанта не указано"}

    if not parties:
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "Список сторон пуст"}

    if not llm.is_available() if hasattr(llm, "is_available") else False:
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "LLM недоступен (API key не задан)"}

    doc_text = _get_doc_text(doc, max_chars=3000)
    if not doc_text.strip():
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": "Не удалось извлечь текст из документа"}

    try:
        result = _call_llm(llm, doc_text, signer_name.strip(),
                           (company or "").strip() or None, parties, resolver_rules)

        llm_party = result.get("party")

        if llm_party is None:
            hint = ""
            if len(signer_name.strip().split()) == 1:
                hint = " Введено только одно слово — попробуйте указать полное ФИО."
            if not company:
                hint += " Можно добавить название компании для уточнения."
            return {
                "party": None,
                "confidence": 0.0,
                "evidence": result.get("evidence", ""),
                "error": (
                    f"Подписант «{signer_name}» не найден ни на одной стороне договора.{hint} "
                    "Используйте режим «По роли» для ручного выбора."
                ),
            }

        valid_names = {p["name"] for p in parties}

        if llm_party not in valid_names:
            mapped = _map_to_known_party(llm_party, parties)
            if mapped:
                result["party"] = mapped
            else:
                return {
                    "party": None,
                    "confidence": 0.0,
                    "evidence": result.get("evidence", ""),
                    "error": (
                        f"Сторона «{llm_party}» не найдена в реестре. "
                        "Добавьте её в parties.json или используйте режим «По роли»."
                    ),
                }

        return {
            "party": result["party"],
            "confidence": float(result.get("confidence", 0.0)),
            "evidence": result.get("evidence", ""),
            "error": None,
        }
    except (LLMError, Exception) as e:
        return {"party": None, "confidence": 0.0, "evidence": "",
                "error": f"LLM error: {e}"}


def _get_doc_text(doc, max_chars: int) -> str:
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


def _build_parties_list_str(parties: list[dict]) -> str:
    lines = []
    for p in parties:
        aliases = p.get("aliases", [])
        display = p.get("display") or p["name"]
        if aliases:
            lines.append(f'- "{p["name"]}" (синонимы/языковые варианты: {", ".join(aliases)}; отображение: {display})')
        else:
            lines.append(f'- "{p["name"]}" ({display})')
    return "\n".join(lines)


def _call_llm(
    llm: LLMClient,
    doc_text: str,
    signer_name: str,
    company: Optional[str],
    parties: list[dict],
    resolver_rules: str,
) -> dict:
    from signfinder.prompts.party_resolver import format_party_resolver

    parties_str = _build_parties_list_str(parties)
    prompt = format_party_resolver(
        doc_text=doc_text,
        signer_name=signer_name,
        company=company,
        parties_str=parties_str,
        resolver_rules=resolver_rules,
    )

    raw = llm.complete(prompt, max_tokens=400)
    raw = re.sub(r"^```(?:json)?", "", raw).strip()
    raw = re.sub(r"```$", "", raw).strip()

    data = json.loads(raw)
    if data.get("party") in (None, "null", ""):
        data["party"] = None
    return data


def _map_to_known_party(llm_name: str, parties: list[dict]) -> Optional[str]:
    if not llm_name:
        return None
    needle = llm_name.strip().lower()
    for p in parties:
        candidates = [p["name"]] + p.get("aliases", [])
        if any(c.lower() == needle for c in candidates):
            return p["name"]
        if any(needle in c.lower() or c.lower() in needle for c in candidates):
            return p["name"]
    return None
