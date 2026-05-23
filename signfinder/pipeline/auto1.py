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
from signfinder.pipeline.settings import get_aliases_for_language, get_markers_for_language
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
) -> tuple:
    """Шаг 3: определить нашу сторону в шапке договора.

    Returns:
        (our_side_dict, None)  — успех
        (None, error_str)      — провал
    """
    aliases = get_aliases_for_language(storage, lang)
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


def run_step4(
    doc: ParsedDocument,
    lang: str,
    our_side: dict,
    storage: StorageBackend,
    llm: LLMClient,
    debug: dict,
) -> tuple:
    """Шаг 4: сгенерировать regex-паттерны для нашей стороны.

    Returns:
        ([patterns], None)  — успех
        (None, error_str)   — провал
    """
    markers_block = get_markers_for_language(storage, lang)
    fragments = _get_strategic_fragments(doc, markers_block)
    prompt = format_generate_regex(
        legal_entity=our_side["legal_entity"],
        roles=our_side["roles"],
        signer=our_side["signer"],
        language=lang,
        markers_block=markers_block,
        strategic_fragments=fragments,
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


# ── Главная точка входа ───────────────────────────────────────────────────────

def run_pipeline_auto_1(
    doc: ParsedDocument,
    language: str,
    storage: StorageBackend,
    llm: LLMClient,
) -> PipelineResult:
    """PipelineAuto1: step3 → step4 → step5 → TextAnchor[].

    Точное соответствие флоу из pages/5_🤖_Авто_подписание.py v1.8.
    Без Streamlit: ошибки возвращаются через PipelineResult.error.

    Параметры:
        doc      — ParsedDocument (уже распарсен)
        language — 'ru'/'en'/'pl'
        storage  — StorageBackend (для signer_profile, markers)
        llm      — LLMClient

    Returns:
        PipelineResult с ok=True и заполненными anchors/matches,
        либо ok=False с error.
    """
    debug: dict = {}

    # Step 3
    our_side, err = run_step3(doc, language, storage, llm, debug)
    if err or our_side is None:
        return PipelineResult(ok=False, error=err, debug=debug)

    # Step 4
    patterns, err = run_step4(doc, language, our_side, storage, llm, debug)
    if err or patterns is None:
        return PipelineResult(ok=False, error=err, our_side=our_side, debug=debug)

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

    # SignMatch → TextAnchor
    anchors: list[TextAnchor] = []
    for m in matches:
        try:
            anchors.append(regex_match_to_anchor(m, m.page, language))
        except Exception as e:
            sys.stderr.write(f"[auto1] regex_match_to_anchor failed for {m.id}: {e}\n")

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
    from signfinder.anchors.finder import apply_template_anchors

    try:
        matches = apply_template_anchors(doc, template)
    except Exception as e:
        sys.stderr.write(f"[auto1] apply_template_anchors: {e}\n")
        return [], []

    anchors: list[TextAnchor] = []
    for m in matches:
        try:
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

    synonyms_used = {
        "legal_entity": our_side.get("legal_entity", ""),
        "roles": our_side.get("roles", []),
        "signer": our_side.get("signer", ""),
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

    return save_template(storage, tpl)
