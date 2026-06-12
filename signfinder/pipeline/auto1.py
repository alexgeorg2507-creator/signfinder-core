"""PipelineAuto1 — оркестратор автоматического подписания.

Точный перенос логики из pages/5_🤖_Авто_подписание.py v1.8
с удалением Streamlit-зависимостей (st.error → PipelineResult.error,
st.session_state → явные параметры и возвраты).

Шаги:
  3. run_step3  — определить нашу сторону в шапке (LLM)
  4. run_step4  — сгенерировать regex-паттерны (LLM)
  5. run_step5  — regex-поиск мест подписи (find_signatures, БЕЗ валидатора)
  +  regex_match_to_anchor для каждого SignMatch

Важно: step5 намеренно НЕ включает LLM-валидатор — это точное соответствие
оригинальному поведению. Валидатор (pipeline.validator) доступен как отдельная
функция для явного вызова после run_pipeline_auto_1 если нужно.
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from signfinder.anchors.finder import find_signatures, regex_match_to_anchor
from signfinder.anchors.models import SignMatch, TextAnchor
from signfinder.llm.base import LLMClient, LLMError
from signfinder.pdf.parser import ParsedDocument
from signfinder.pipeline.settings import (
    get_aliases_for_language,
    get_markers_for_language,
    get_markers_for_languages,
)
from signfinder.prompts.extraction import format_find_our_side
from signfinder.prompts.regex_generation import format_generate_regex
from signfinder.storage.base import StorageBackend


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    """Результат run_pipeline_auto_1.

    ok=True  → anchors и matches заполнены.
    ok=False → error содержит сообщение для показа пользователю,
               debug может быть частично заполнен (для диагностики).
    """
    ok: bool
    error: Optional[str] = None
    our_side: Optional[dict] = None       # {legal_entity, roles, signer, confidence, ...}
    patterns: list = field(default_factory=list)     # list[str]
    matches: list = field(default_factory=list)      # list[SignMatch]
    anchors: list = field(default_factory=list)      # list[TextAnchor]
    debug: dict = field(default_factory=dict)        # prompt_step3/raw_step3/step4 и т.п.


# ── Токенизатор алиасов (точная копия из исходника) ───────────────────────────

def _extract_distinctive_tokens(s: str) -> list:
    """Извлекает уникальные различительные токены из строки имени/роли.

    Точная копия из pages/5_🤖_Авто_подписание.py:_extract_distinctive_tokens.
    Используется для формирования other_aliases в run_step5.
    """
    if not s:
        return []
    sl = s.lower().strip()
    if sl in ("не указан", "не указана", "не указано", "—", "-", "n/a", "na", ""):
        return []
    tokens = []
    # Слова в кавычках
    for m in re.finditer(r'[«"\']([^»"\']+)[»"\']', s):
        inner = m.group(1).strip()
        if len(inner) >= 3:
            tokens.append(inner)
            for w in inner.split():
                if len(w) >= 4:
                    tokens.append(w)
    # Заглавные слова (кириллица + латиница) — без стоп-слов
    _stop = {
        "общество", "ограниченной", "ответственностью", "компания",
        "корпорация", "генеральный", "директор", "лице", "именуем",
        "именуемая", "именуемое", "именуемый", "далее", "стороны",
        "стороне", "договор", "договору",
    }
    for w in re.findall(r"[А-ЯA-ZЁ][а-яa-zА-ЯA-ZёЁ\-]{3,}", s):
        if w.lower() not in _stop:
            tokens.append(w)
    seen, result = set(), []
    for t in tokens:
        tl = t.lower()
        if tl not in seen:
            seen.add(tl)
            result.append(t)
    return result


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_header_text(doc: ParsedDocument) -> str:
    parts = []
    total = 0
    for i, page in enumerate(doc.pages):
        text = page.text or ""
        parts.append(f"--- Страница {i + 1} ---\n{text}")
        total += len(text)
        if i >= 2 or total >= 3000:
            break
    return "\n".join(parts)[:4000]


def _deduplicate_patterns(patterns: list[str], max_count: int = 20) -> list[str]:
    """Убрать семантические дубли и ограничить число паттернов.

    Нормализация: убираем пробелы и приводим к нижнему регистру для сравнения —
    паттерны отличающиеся только капитализацией или пробелами считаются дублями.
    Порядок сохраняется (первый вариант побеждает).
    """
    seen: set[str] = set()
    result: list[str] = []
    for p in patterns:
        norm = re.sub(r"\s+", "", p.lower())
        if norm not in seen:
            seen.add(norm)
            result.append(p)
        if len(result) >= max_count:
            break
    return result


def _get_strategic_fragments(doc: ParsedDocument, markers_block: dict) -> str:
    pages = doc.pages
    n = len(pages)
    anchors_kw = [a.lower() for a in markers_block.get("section_anchors", [])]
    fragments = []
    if pages:
        fragments.append(f"=== ПЕРВАЯ СТРАНИЦА ===\n{pages[0].text or ''}")
    if n > 1:
        fragments.append(f"=== ПОСЛЕДНЯЯ СТРАНИЦА ===\n{pages[-1].text or ''}")
    for i in range(1, n - 1):
        text = (pages[i].text or "").lower()
        if any(a in text for a in anchors_kw):
            fragments.append(f"=== СТРАНИЦА {i+1} ===\n{pages[i].text[:2000]}")
    footer_parts = []
    for i, page in enumerate(pages):
        text = (page.text or "").strip()
        footer = text[-200:] if len(text) > 200 else text
        if footer.strip():
            footer_parts.append(f"[стр.{i+1}] {footer}")
    if footer_parts:
        fragments.append("=== ФУТЕРЫ ===\n" + "\n---\n".join(footer_parts[:20]))
    return "\n\n".join(fragments)[:8000]


def _call_llm_json(
    llm: LLMClient,
    prompt: str,
    max_tokens: int,
    debug: dict,
    capture_key: str,
) -> Optional[dict]:
    """Вызов LLM с парсингом JSON. Складывает prompt/raw в debug dict.

    Точная копия логики _call_llm_json из оригинала, но через LLMClient.
    """
    import json as _json

    debug[f"prompt_{capture_key}"] = prompt
    try:
        raw = llm.complete(prompt, max_tokens=max_tokens)
    except LLMError as e:
        sys.stderr.write(f"[auto1] LLM error in {capture_key}: {e}\n")
        debug[f"raw_{capture_key}"] = f"<LLMError: {e}>"
        return None

    debug[f"raw_{capture_key}"] = raw

    cleaned = re.sub(r"^```(?:json)?", "", raw, flags=re.MULTILINE).strip()
    cleaned = re.sub(r"```$", "", cleaned, flags=re.MULTILINE).strip()
    m = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if m:
        cleaned = m.group(0)
    try:
        return _json.loads(cleaned)
    except _json.JSONDecodeError as e:
        sys.stderr.write(f"[auto1] JSON parse error in {capture_key}: {e}\n")
        return None


# ── Steps ─────────────────────────────────────────────────────────────────────

def run_step3(
    doc: ParsedDocument,
    lang: str,
    storage: StorageBackend,
    llm: LLMClient,
    debug: dict,
    signer_id: str = "default",
) -> tuple:
    """Шаг 3: определить нашу сторону в шапке договора.

    Returns:
        (our_side_dict, None)  — успех
        (None, error_str)      — провал
    """
    aliases = get_aliases_for_language(storage, lang, signer_id=signer_id)
    markers_block = get_markers_for_language(storage, lang)

    if not aliases["signer"]:
        return None, "Шаг 3: Не задан алиас ФИО подписанта. Заполните Настройки (signer_profile)."

    header = _get_header_text(doc)
    prompt = format_find_our_side(
        header_text=header,
        language=lang,
        company_aliases=aliases["company"],
        signer_aliases=aliases["signer"],
        markers=markers_block,
    )
    result = _call_llm_json(llm, prompt, max_tokens=1500, debug=debug, capture_key="step3")
    if result is None:
        return None, "Шаг 3: LLM не ответил или невалидный JSON."

    confidence = float(result.get("confidence", 0))
    our_index = result.get("our_side_index")
    synonyms = result.get("our_side_synonyms") or {}

    if our_index is None or confidence < 0.5:
        return None, "Шаг 3: Наша сторона не найдена в шапке договора."

    return {
        "legal_entity": synonyms.get("legal_entity", ""),
        "roles": synonyms.get("roles", []),
        "signer": synonyms.get("signer", ""),
        "confidence": confidence,
        "match_reason": result.get("match_reason", ""),
        "evidence": result.get("evidence", ""),
        "all_parties": result.get("all_parties", []),
    }, None


def _build_other_side_names(our_side: dict) -> list[str]:
    """Собрать список имён другой стороны из all_parties для промпта step4.

    Используется как запрещённые токены в промпте генерации паттернов —
    предотвращает генерацию паттернов вида «ООО Инлайн[\s\S]{0,50}_{3,}».
    """
    our_entity = (our_side.get("legal_entity") or "").strip().lower()
    our_roles = {r.strip().lower() for r in (our_side.get("roles") or []) if r}
    our_signer = (our_side.get("signer") or "").strip().lower()

    other_names: list[str] = []
    seen: set[str] = set()

    for p in (our_side.get("all_parties") or []):
        if not isinstance(p, dict):
            continue
        le = (p.get("legal_entity") or "").strip()
        role = (p.get("role") or "").strip()
        signer_p = (p.get("signer") or "").strip()
        # Пропускаем нашу сторону
        if le and le.lower() == our_entity:
            continue
        if role and role.lower() in our_roles:
            continue
        for val in [le, role, signer_p]:
            if val and val.lower() not in seen:
                seen.add(val.lower())
                other_names.append(val)

    return other_names


def _extract_surnames(storage: StorageBackend, language: str, our_side: dict, signer_id: str = "default") -> list[str]:
    """Собрать фамилии НАШЕГО подписанта из signer_profile и our_side.

    Фамилия = первое слово с заглавной буквы (≥3 симв). Из конфига алиасы
    идут как 'Лебедев, Лебедев А, ...' → split → 'Лебедев'.
    """
    surnames: list[str] = []
    seen: set[str] = set()

    def _add(word: str) -> None:
        w = word.strip().strip(".,/()«»\"'")
        if len(w) >= 3 and w[0].isupper() and w.lower() not in seen:
            seen.add(w.lower())
            surnames.append(w)

    # 1. Из конфига signer_profile (надёжнее — без ролей-префиксов)
    try:
        aliases = get_aliases_for_language(storage, language, signer_id=signer_id)
        for alias in aliases.get("signer", []):
            for tok in alias.replace(",", " ").split():
                _add(tok)
                break  # первое слово алиаса = фамилия
    except Exception as e:
        sys.stderr.write(f"[auto1] _extract_surnames aliases: {e}\n")

    # 2. Из our_side.signer (синоним в документе) — слова с заглавной без точек-инициалов
    signer_doc = (our_side.get("signer") or "")
    _role_words = {"генеральный", "директор", "руководитель", "представитель",
                   "управляющий", "президент", "главный", "заместитель"}
    for tok in signer_doc.split():
        tl = tok.strip(".,/()").lower()
        if tl in _role_words or "." in tok:
            continue
        _add(tok)

    return surnames


def _signer_underscore_patterns(storage: StorageBackend, language: str, our_side: dict, signer_id: str = "default") -> list[str]:
    """Детерминированные паттерны '_{3,} Фамилия' по фамилии подписанта.

    Заякорены на ПОДЧЁРКИВАНИИ (начинаются с '_') → подпись садится на линию
    подчёркивания рядом с нашим подписантом, а не на строку с именем компании.
    Корректно выбирают нужный столбец в двухколоночном блоке подписей.
    """
    patterns: list[str] = []
    for surname in _extract_surnames(storage, language, our_side, signer_id=signer_id):
        esc = re.escape(surname)
        # Однострочный (русский формат): ____ Лебедев
        patterns.append(rf"_{{3,}}[^\n]{{0,20}}{esc}")
        # Многострочный (PL/MK формат): ____\nВадим Борисов
        patterns.append(rf"_{{3,}}\n[^\n]{{0,20}}{esc}")
    return patterns


def _extract_contract_type(doc: ParsedDocument, language: str) -> str:
    """Детерминированное извлечение типа договора из заголовка первой страницы."""
    import re as _re
    text = ""
    if doc.pages:
        text = (doc.pages[0].text or "")[:800]

    if language == "ru":
        m = _re.search(
            r"ДОГОВОР\s+((?:[А-ЯЁА-яёa-z][А-ЯЁА-яёa-z\-]*\s+){0,3}[А-ЯЁА-яёa-z][А-ЯЁА-яёa-z\-]*)",
            text,
            _re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip()
            raw = _re.sub(r"\s*[№NnNnNn]?\s*\d.*$", "", raw).strip()
            raw = _re.sub(r"\s*(от|г\.|года).*$", "", raw, flags=_re.IGNORECASE).strip()
            if 2 <= len(raw.split()) <= 5:
                return "Договор " + raw.lower()
        return "Договор"

    elif language == "en":
        m = _re.search(
            r"(SERVICE\s+AGREEMENT|SUPPLY\s+AGREEMENT|LEASE\s+AGREEMENT|"
            r"CONTRACT\s+FOR\s+[A-Z][A-Za-z\s]{2,25}|AGREEMENT\s+(?:FOR|ON|OF)\s+[A-Z][A-Za-z\s]{2,25})",
            text, _re.IGNORECASE,
        )
        if m:
            return m.group(0).strip()[:50].title()
        return "Contract"

    elif language == "pl":
        m = _re.search(
            r"(UMOWA\s+(?:[A-ZŁĄĆĘÓŚŻŹ][A-ZŁĄĆĘÓŚŻŹa-złąćęóśżź\-]*\s+){0,3}"
            r"[A-ZŁĄĆĘÓŚŻŹ][A-ZŁĄĆĘÓŚŻŹa-złąćęóśżź\-]*)",
            text, _re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip()
            raw = _re.sub(r"\s*[nrNR]?\s*\d.*$", "", raw).strip()
            if 1 <= len(raw.split()) <= 5:
                return raw.capitalize()
        return "Umowa"

    return "Договор"


def _extract_counterparty(our_side: dict) -> str:
    """Извлечь название контрагента (другой стороны) из all_parties."""
    our_entity = (our_side.get("legal_entity") or "").strip().lower()
    our_roles = {r.strip().lower() for r in (our_side.get("roles") or []) if r}

    for p in (our_side.get("all_parties") or []):
        if not isinstance(p, dict):
            continue
        le = (p.get("legal_entity") or "").strip()
        role = (p.get("role") or "").strip()
        if le and le.lower() == our_entity:
            continue
        if role and role.lower() in our_roles:
            continue
        if le:
            return le
        if role:
            return role

    return ""


def run_step4(
    doc: ParsedDocument,
    lang: str,
    our_side: dict,
    storage: StorageBackend,
    llm: LLMClient,
    debug: dict,
    markers_override: dict | None = None,
) -> tuple:
    """Шаг 4: сгенерировать regex-паттерны для нашей стороны.

    Returns:
        ([patterns], None)  — успех
        (None, error_str)   — провал
    """
    markers_block = markers_override if markers_override is not None \
        else get_markers_for_language(storage, lang)
    fragments = _get_strategic_fragments(doc, markers_block)
    other_side_names = _build_other_side_names(our_side)
    prompt = format_generate_regex(
        legal_entity=our_side["legal_entity"],
        roles=our_side["roles"],
        signer=our_side["signer"],
        language=lang,
        markers_block=markers_block,
        strategic_fragments=fragments,
        other_side_names=other_side_names or None,
    )
    result = _call_llm_json(llm, prompt, max_tokens=3000, debug=debug, capture_key="step4")
    if result is None:
        return None, "Шаг 4: LLM не вернул паттерны."

    raw_patterns = result.get("patterns", [])
    patterns = []
    for item in raw_patterns:
        pat = item.get("pattern", "") if isinstance(item, dict) else str(item)
        pat = pat.strip()
        if pat:
            try:
                re.compile(pat, re.IGNORECASE | re.UNICODE)
                patterns.append(pat)
            except re.error as e:
                sys.stderr.write(f"[auto1] bad pattern '{pat}': {e}\n")

    if not patterns:
        return None, "Шаг 4: Не удалось сгенерировать валидные паттерны."
    return patterns, None


def run_step5(
    doc: ParsedDocument,
    our_side: dict,
    patterns: list,
) -> Optional[list]:
    """Шаг 5: regex-поиск мест подписи.

    ТОЧНОЕ соответствие оригиналу: только find_signatures, БЕЗ LLM-валидатора.
    Возвращает list[SignMatch] или None если ничего не найдено.

    Логика other_aliases — точная копия _run_step5 из оригинального файла,
    включая _extract_distinctive_tokens для токенизации.
    """
    our_entity = (our_side.get("legal_entity") or "").strip()
    our_roles = set(r.strip().lower() for r in our_side.get("roles", []) if r)
    our_signer = (our_side.get("signer") or "").strip()
    our_signer_tokens = set(t.lower() for t in _extract_distinctive_tokens(our_signer))
    our_entity_tokens = set(t.lower() for t in _extract_distinctive_tokens(our_entity))

    other_aliases: list = []
    for p in our_side.get("all_parties", []):
        if not isinstance(p, dict):
            continue
        le = (p.get("legal_entity") or "").strip()
        role = (p.get("role") or "").strip()
        signer_p = (p.get("signer") or "").strip()
        # Пропускаем нашу же сторону по legal_entity
        if le and le == our_entity:
            continue
        if role and role.lower() not in our_roles:
            other_aliases.append(role)
        for t in _extract_distinctive_tokens(le):
            if t.lower() not in our_entity_tokens:
                other_aliases.append(t)
        for t in _extract_distinctive_tokens(signer_p):
            if t.lower() not in our_signer_tokens:
                other_aliases.append(t)

    seen: set = set()
    other_aliases_clean: list = []
    for a in other_aliases:
        al = a.lower().strip()
        if len(al) >= 3 and al not in seen:
            seen.add(al)
            other_aliases_clean.append(a)

    party_dict = {
        "name": our_entity or "auto",
        "display": our_entity or "auto",
        "aliases": (
            ([our_entity] if our_entity else [])
            + (our_side.get("roles") or [])
            + ([our_signer] if our_signer else [])
        ),
        "signer": our_signer,
        "other_aliases": other_aliases_clean,
        "patterns": patterns,
        "notes": "",
    }

    matches = find_signatures(doc, party_dict)
    if not matches:
        return None
    return matches


def _add_reverse_dot_patterns(
    final_patterns: list,
    our_side,
) -> list:
    """Добавить паттерны \.{5,}\\nX для случаев когда точечная линия ПЕРЕД названием.

    IndividualProject формат:
      ......................................................
      Innowise Sp. z o.o (d/b/a Innowise Group)
      (Agent)
    Нужен паттерн: \\.{5,}\\n[^\\n]{0,15}Innowise
    Работает для всех документов, не только dual_column.
    """
    if not our_side:
        return final_patterns

    anchors_to_check = []
    le = (our_side.get("legal_entity") or "").strip()
    if le:
        anchors_to_check.append(le[:15])
    for role in (our_side.get("roles") or []):
        r = (role or "").strip()
        if r and len(r) > 3:
            anchors_to_check.append(r)

    extras = []
    for anchor in anchors_to_check:
        try:
            esc = re.escape(anchor)
            p_dot_nl = rf"\.{{5,}}\n[^\n]{{0,15}}{esc}"
            p_dot_same = rf"\.{{5,}}[^\n]{{0,30}}{esc}"
            for p in [p_dot_nl, p_dot_same]:
                re.compile(p, re.IGNORECASE | re.UNICODE)
                if p not in final_patterns:
                    extras.append(p)
        except re.error:
            pass

    return final_patterns + extras


def _add_docusign_tab_patterns(
    final_patterns: list,
    our_side: dict,
    page_texts: list[str],
) -> list:
    r"""Добавить паттерны для DocuSign tab-маркеров (\t1\, \s1\, \e1\).

    DocuSign вставляет в текст PDF маркеры вида \t1\ (SignHere), \e1\ (DateSigned),
    \s1\ (InitialHere). Пара \t1\ + \e1\ — это ОДНО место подписи.
    Используем только \t (SignHere) как якорь, чтобы не дублировать.

    Формат страницы 4 IndividualProject:
      \t1\
      \e1\
      Place, date
      Innowise Sp. z o.o (d/b/a Innowise Group)
      (Agent)

    Паттерн: \\t\d+\\...Company (через 1-4 строки)
    """
    if not our_side:
        return final_patterns

    # Проверяем наличие DocuSign маркеров в тексте документа
    has_tabs = any(re.search(r"\\[tse]\d+\\", pt) for pt in page_texts)
    if not has_tabs:
        return final_patterns

    anchors_to_check = []
    le = (our_side.get("legal_entity") or "").strip()
    if le:
        anchors_to_check.append(le[:15])
    for role in (our_side.get("roles") or []):
        r = (role or "").strip()
        if r and len(r) > 3:
            anchors_to_check.append(r)

    extras = []
    for anchor in anchors_to_check:
        try:
            esc = re.escape(anchor)
            # \t маркер, затем 1-4 строки, затем название компании/роль
            p = rf"\\t\d+\\[^\n]*\n(?:[^\n]*\n){{0,3}}[^\n]{{0,50}}{esc}"
            re.compile(p, re.IGNORECASE | re.UNICODE)
            if p not in final_patterns:
                extras.append(p)
        except re.error:
            pass

    return final_patterns + extras


def _filter_by_our_side_context(
    matches: list,
    page_texts: list,
    our_side: dict,
    trusted_patterns: "set[str] | None" = None,
) -> list:
    """Оставить только матчи где в 80 символах ПЕРЕД якорем есть наши синонимы.

    trusted_patterns — паттерны которые всегда доверенные (signer_pats):
    они уже заякорены на ФИО нашего подписанта, контекст проверять не нужно.

    Смотрим только назад — не вперёд. Это исключает ситуацию когда Innowise
    стоит ПОСЛЕ клиентского якоря и попадает в двунаправленное окно.

    Структура текста: КЛИЕНТ_ЯКОРЬ ... Innowise_ЯКОРЬ
    → для клиентского якоря 80 символов назад — нет Innowise → фильтруется.
    → для Innowise якоря 80 символов назад — есть "Innowise Group:" → остаётся.
    """
    if not our_side or not matches:
        return matches

    synonyms = set()
    le = (our_side.get("legal_entity") or "").strip().lower()
    if le:
        synonyms.add(le[:10])
    for role in (our_side.get("roles") or []):
        r = (role or "").strip().lower()
        if r and len(r) > 3:
            synonyms.add(r)
    signer = (our_side.get("signer") or "").strip().lower()
    if signer:
        synonyms.add(signer[:8])

    if not synonyms:
        return matches

    result = []
    for m in matches:
        # Доверенные паттерны (по ФИО подписанта) — не фильтруем, они и так специфичны
        if trusted_patterns and getattr(m, "pattern", "") in trusted_patterns:
            result.append(m)
            continue
        page_idx = getattr(m, "page_hint", None) or getattr(m, "page", None)
        if page_idx is None:
            result.append(m)
            continue
        try:
            page_text = page_texts[int(page_idx)].lower()
        except (IndexError, TypeError):
            result.append(m)
            continue

        # SignMatch использует 'context', TextAnchor — 'anchor_text'
        ctx_text = (getattr(m, "context", "") or getattr(m, "anchor_text", "") or "").strip().lower()
        match_pos = page_text.find(ctx_text[:20]) if ctx_text else -1

        if match_pos == -1:
            result.append(m)
            continue

        ctx_start = max(0, match_pos - 80)
        ctx_end = match_pos  # только то что ПЕРЕД якорем
        ctx = page_text[ctx_start:ctx_end]

        if any(syn in ctx for syn in synonyms):
            result.append(m)
        # else: фильтруем — это блок другой стороны

    return result


def _drop_marker_only_when_signer_present(matches: list, signer_pat_set: "set[str]") -> list:
    """Убрать company/marker-only матчи из колонки, где есть signer-anchored матч.

    Имя подписанта (паттерн `_{3,}…Фамилия`) однозначно наше. Маркер ('ОВЛАСТЕНО
    ЛИЦЕ', 'УПРАВИТЕЛ', и т.п.) может принадлежать ДРУГОЙ стороне (в двуязычном
    блоке подписи клиент и мы используют разные маркеры в одной колонке).

    Если в той же колонке (X-перекрытие) на той же странице есть матч, заякоренный
    на ФИО нашего подписанта — marker/company-only матч этой колонки удаляется.
    Это снимает ложную подпись на строке клиента ('ДОО … ОВЛАСТЕНО ЛИЦЕ ___').
    """
    if not signer_pat_set or not matches:
        return matches
    result = []
    for m in matches:
        if getattr(m, "pattern", "") in signer_pat_set:
            result.append(m)
            continue
        m_x0, m_x1 = m.bbox[0], m.bbox[2]
        shadowed = any(
            getattr(o, "pattern", "") in signer_pat_set
            and o.page == m.page
            and min(o.bbox[2], m_x1) > max(o.bbox[0], m_x0)  # X-перекрытие = одна колонка
            for o in matches
        )
        if not shadowed:
            result.append(m)
    return result


def _cluster_signature_blocks(
    matches: list,
    aliases_ordered: list[str],
    y_radius: float = 60.0,
) -> list:
    """Сгруппировать матчи в блоки подписи, выбрать по одному на блок.

    Блок = матчи на одной странице в пределах y_radius по вертикали с X-перекрытием.
    Победитель в блоке — чей контекст содержит синоним, стоящий РАНЬШЕ в
    aliases_ordered (порядок синонимов в профиле = приоритет оператора).
    Если приоритет равный — самый широкий bbox (полная линия, не фрагмент).
    """
    if not matches:
        return matches

    # Приоритет синонима: индекс в aliases_ordered (меньше = важнее)
    def _synonym_rank(m) -> int:
        ctx = (getattr(m, "context", "") or "").lower()
        for i, alias in enumerate(aliases_ordered):
            if alias and alias.lower()[:15] in ctx:
                return i
        return len(aliases_ordered)  # не нашли синоним → низший приоритет

    def _bbox_width(m) -> float:
        b = m.bbox
        return b[2] - b[0]

    # Группировка по странице + вертикальной близости
    clusters: list[list] = []
    for m in sorted(matches, key=lambda x: (x.page, (x.bbox[1] + x.bbox[3]) / 2)):
        m_yc = (m.bbox[1] + m.bbox[3]) / 2
        placed = False
        for cluster in clusters:
            ref = cluster[0]
            if ref.page != m.page:
                continue
            ref_yc = (ref.bbox[1] + ref.bbox[3]) / 2
            # X-перекрытие: матчи одной колонки/блока
            x_overlap = min(m.bbox[2], ref.bbox[2]) > max(m.bbox[0], ref.bbox[0])
            if abs(m_yc - ref_yc) <= y_radius and x_overlap:
                cluster.append(m)
                placed = True
                break
        if not placed:
            clusters.append([m])

    # Выбор победителя в каждом кластере
    winners = []
    for cluster in clusters:
        winner = min(cluster, key=lambda m: (_synonym_rank(m), -_bbox_width(m)))
        winners.append(winner)
    return winners


# ── Главная точка входа ───────────────────────────────────────────────────────

def run_pipeline_auto_1(
    doc: ParsedDocument,
    language: str,
    storage: StorageBackend,
    llm: LLMClient,
    signer_id: str = "default",
) -> PipelineResult:
    """PipelineAuto1: step3 → step4 → step5 → TextAnchor[].

    Точное соответствие флоу из pages/5_🤖_Авто_подписание.py v1.8.
    Без Streamlit: ошибки возвращаются через PipelineResult.error.

    Параметры:
        doc       — ParsedDocument (уже распарсен)
        language  — 'ru'/'en'/'pl'
        storage   — StorageBackend (для signer_profile, markers)
        llm       — LLMClient
        signer_id — id профиля подписанта (Модель Б)

    Returns:
        PipelineResult с ok=True и заполненными anchors/matches,
        либо ok=False с error.
    """
    debug: dict = {}

    # Для двуязычных документов: объединить маркеры всех языков
    # и передать LLM составной хинт ("en, mk" вместо "en").
    doc_languages = getattr(doc, "languages", []) or [language]
    if len(doc_languages) > 1:
        effective_language = ", ".join(doc_languages)
        effective_markers = get_markers_for_languages(storage, doc_languages)
    else:
        effective_language = language
        effective_markers = get_markers_for_language(storage, language)

    debug["effective_language"] = effective_language
    debug["doc_languages"] = doc_languages

    # Step 3
    our_side, err = run_step3(doc, effective_language, storage, llm, debug, signer_id=signer_id)
    if err or our_side is None:
        return PipelineResult(ok=False, error=err, debug=debug)

    # Step 4
    patterns, err = run_step4(doc, effective_language, our_side, storage, llm, debug,
                              markers_override=effective_markers)
    if err or patterns is None:
        return PipelineResult(ok=False, error=err, our_side=our_side, debug=debug)

    # ── Сборка итогового пула паттернов ──────────────────────────────────────
    # Приоритет: детерминированные паттерны по фамилии подписанта (заякорены на
    # подчёркивании) → корректная позиция подписи в блоке '____ Фамилия'.
    signer_pats = _signer_underscore_patterns(storage, effective_language, our_side, signer_id=signer_id)
    debug["signer_underscore_patterns"] = signer_pats

    # Нормализация кросс-строчных LLM-паттернов: '[\s\S]' (любой символ, включая
    # перенос строки) → '[^\n]' (любой символ КРОМЕ переноса).
    # Зачем: паттерн 'Роль[\s\S]{0,50}_{3,}' нужен для подвалов вида
    # 'Клиент ________' или 'Исполнитель: ________' (роль и подчёркивание на ОДНОЙ
    # строке, между ними могут быть двоеточия/пробелы) — он ставит подпись после
    # роли. Но тот же '[\s\S]' в двухколоночном блоке 'Компания\n____ ФИО'
    # тянется через перенос и садится на строку компании.
    # '[^\n]' сохраняет нужный одно-строчный случай и убирает кросс-строчный.
    def _normalize_sameline(p: str) -> str:
        return p.replace("[\\s\\S]", "[^\\n]").replace("[\\S\\s]", "[^\\n]")

    normalized_llm: list[str] = []
    norm_count = 0
    for p in patterns:
        np = _normalize_sameline(p)
        if np != p:
            norm_count += 1
        try:
            re.compile(np, re.IGNORECASE | re.UNICODE)
            normalized_llm.append(np)
        except re.error:
            sys.stderr.write(f"[auto1] bad normalized pattern '{np}'\n")
    debug["patterns_crossline_normalized"] = norm_count
    normalized_llm = _deduplicate_patterns(normalized_llm, max_count=20)
    debug["patterns_after_dedup"] = len(normalized_llm)

    # Структурные паттерны из markers (name-independent, '_{3,} (...)')
    markers_block = effective_markers
    structural = []
    for sp in markers_block.get("signature_block_patterns", []):
        try:
            re.compile(sp, re.IGNORECASE | re.UNICODE)
            structural.append(sp)
        except re.error:
            sys.stderr.write(f"[auto1] bad structural pattern '{sp}'\n")

    final_patterns: list[str] = []
    for p in signer_pats + normalized_llm + structural:
        if p and p not in final_patterns:
            final_patterns.append(p)

    if final_patterns:
        patterns = final_patterns
    else:
        sys.stderr.write("[auto1] WARNING: no safe patterns, keeping originals\n")

    # Многострочные паттерны для документов с вертикальной компоновкой подписи
    # (роль\n___\nимя вместо роль _____ имя)
    if getattr(doc, "layout", "single_column") == "dual_column_vertical":
        multiline_extra = []
        for mw in effective_markers.get("marker_words", []):
            esc_mw = re.escape(mw)
            p = rf"{esc_mw}[^\n]{{0,10}}\n[^\n]{{0,10}}_{{3,}}"
            try:
                re.compile(p, re.IGNORECASE | re.UNICODE)
                if p not in final_patterns:
                    multiline_extra.append(p)
            except re.error:
                pass
        final_patterns.extend(multiline_extra)
        patterns = final_patterns
        debug["multiline_extra_patterns"] = multiline_extra

    # Обратные паттерны для точечных линий (\.{5,} → название компании)
    # Работает для всех документов, не только dual_column
    final_patterns = _add_reverse_dot_patterns(final_patterns, our_side)
    patterns = final_patterns

    # DocuSign tab-маркеры (\t1\, \e1\) — место подписи без текстовых подчёркиваний
    page_texts_for_tabs = [p.text or "" for p in doc.pages]
    final_patterns = _add_docusign_tab_patterns(final_patterns, our_side, page_texts_for_tabs)
    patterns = final_patterns

    debug["final_patterns"] = final_patterns

    # Step 5 — только find_signatures, без валидатора
    matches = run_step5(doc, our_side, patterns)
    if matches is None:
        return PipelineResult(
            ok=False,
            error="Шаг 5: Паттерны сгенерированы, но мест подписи не найдено.",
            our_side=our_side,
            patterns=patterns,
            debug=debug,
        )

    debug["step5_matches_count"] = len(matches)

    # Фильтр по нашей стороне — ТОЛЬКО для dual_column_vertical.
    # Цель: убрать ложные блоки КЛИЕНТА когда на одной странице два похожих блока.
    # Для single_column не нужен: step3 уже нашёл нашу сторону, паттерны корректны.
    if our_side and getattr(doc, "layout", "single_column") == "dual_column_vertical":
        matches = _filter_by_our_side_context(
            matches, [p.text for p in doc.pages], our_side,
            trusted_patterns=set(signer_pats),
        )
        debug["our_side_filter"] = {
            "applied": True,
            "anchors_after_filter": len(matches),
        }
    else:
        debug["our_side_filter"] = {
            "applied": False,
            "reason": "single_column — filter skipped",
        }

    if not matches:
        return PipelineResult(
            ok=False,
            error="Шаг 5: После фильтрации по контексту нашей стороны мест подписи не осталось.",
            our_side=our_side,
            patterns=patterns,
            debug=debug,
        )

    # Приоритет ФИО подписанта: в колонке, где есть матч заякоренный на нашем
    # подписанте, убираем company/marker-only матчи (маркер мог принадлежать
    # клиенту — 'ДОО … ОВЛАСТЕНО ЛИЦЕ ___' в двуязычном блоке).
    before_signer_pref = len(matches)
    matches = _drop_marker_only_when_signer_present(matches, set(signer_pats))
    debug["signer_priority_drop"] = {
        "before": before_signer_pref,
        "after": len(matches),
    }

    # Кластеризация блоков подписи + выбор по приоритету синонима.
    # Якоря в вертикальном радиусе ~60pt с X-перекрытием = ОДИН блок = ОДНА подпись.
    # Победитель — чей синоним РАНЬШЕ в порядке профиля (company → signer → roles).
    if our_side:
        aliases_ordered: list[str] = []
        le = our_side.get("legal_entity", "")
        if le:
            aliases_ordered.append(le)
        signer = our_side.get("signer", "")
        if signer:
            aliases_ordered.append(signer)
        for r in (our_side.get("roles") or []):
            if r:
                aliases_ordered.append(r)

        before_cluster = len(matches)
        matches = _cluster_signature_blocks(matches, aliases_ordered)
        debug["clustering"] = {
            "before": before_cluster,
            "after": len(matches),
            "aliases_order": aliases_ordered,
        }

    # SignMatch → TextAnchor
    anchors: list[TextAnchor] = []
    empty_pattern = 0
    for m in matches:
        try:
            anchor = regex_match_to_anchor(m, m.page, language)
            if not (anchor.generated_pattern or "").strip():
                empty_pattern += 1
            anchors.append(anchor)
        except Exception as e:
            sys.stderr.write(f"[auto1] regex_match_to_anchor failed for {m.id}: {e}\n")
    # ФИКС 1 (диагностика): сколько якорей ушло с пустым pattern.
    # generated_pattern нужен overlay (_find_underscore_anchor) чтобы понять тип
    # привязки (начинается с '_' → x0; иначе текст-префикс). Пустой → fallback case 5.
    debug["anchors_empty_pattern"] = empty_pattern

    return PipelineResult(
        ok=True,
        our_side=our_side,
        patterns=patterns,
        matches=matches,
        anchors=anchors,
        debug=debug,
    )


# ── Применение шаблона (зелёный свет) ────────────────────────────────────────

def apply_template_to_doc(
    doc: ParsedDocument,
    template,
    language: str,
) -> tuple:
    """Применить шаблон к документу.

    Соответствует _apply_template_anchors_to_session из оригинала, но
    без session_state — возвращает (matches, anchors) напрямую.

    Returns:
        (list[SignMatch], list[TextAnchor])
        При провале — ([], [])
    """
    from signfinder.anchors.finder import apply_template_anchors, manual_match_to_anchor

    try:
        matches = apply_template_anchors(doc, template)
    except Exception as e:
        sys.stderr.write(f"[auto1] apply_template_anchors: {e}\n")
        return [], []

    anchors: list[TextAnchor] = []
    for m in matches:
        try:
            # v1.18.3: ручные якоря сохраняют провенанс manual_click — иначе при
            # повторном сохранении шаблона они деградируют в auto и подпись «уезжает».
            if getattr(m, "added_by", "auto_regex") == "manual_click":
                anchors.append(manual_match_to_anchor(m, m.page))
            else:
                anchors.append(regex_match_to_anchor(m, m.page, language))
        except Exception as e:
            sys.stderr.write(f"[auto1] template anchor conv: {e}\n")

    return matches, anchors


# ── Сохранение шаблона ────────────────────────────────────────────────────────

def save_pipeline_template(
    doc: ParsedDocument,
    language: str,
    our_side: dict,
    anchors: list,
    storage: StorageBackend,
    template_name: Optional[str] = None,
    signature_scale: float = 1.0,
) -> str:
    """Создать и сохранить DocumentTemplate после pipeline.

    Соответствует _save_template() из оригинала (без Streamlit).

    Returns:
        template_id (str)

    Raises:
        Exception при ошибке сохранения.
    """
    import fitz
    from dataclasses import asdict

    from signfinder.fingerprint import compute_fingerprint
    from signfinder.templates.storage import new_template, save_template

    has_manual = any(
        getattr(a, "added_by", None) == "manual_click"
        for a in anchors
    )

    fitz_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")
    try:
        fp = compute_fingerprint(fitz_doc, language)
    finally:
        fitz_doc.close()

    contract_type = _extract_contract_type(doc, language)
    counterparty = _extract_counterparty(our_side)

    synonyms_used = {
        "legal_entity": our_side.get("legal_entity", ""),
        "roles": our_side.get("roles", []),
        "signer": our_side.get("signer", ""),
        "contract_type": contract_type,
        "counterparty": counterparty,
    }

    tpl = new_template(
        language=language,
        anchors=[asdict(a) if not isinstance(a, dict) else a for a in anchors],
        fingerprint=fp,
        synonyms_used=synonyms_used,
        created_by="manual_enrichment" if has_manual else "pipeline_auto_1",
    )
    if template_name:
        tpl.name = template_name
    tpl.signature_scale = signature_scale

    return save_template(storage, tpl)
