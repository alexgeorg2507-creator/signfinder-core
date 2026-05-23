"""Поиск мест подписи в распарсенном документе.

Переехало из core/finder.py.
Изменения:
  - SignMatch перенесён в anchors/models.py
  - TextAnchor импортируется из anchors/models
  - ParsedDocument импортируется из signfinder.pdf.parser
  - Алгоритмы НЕ менялись
"""
from __future__ import annotations

import re
import sys

try:
    import fitz
except ImportError:
    fitz = None  # type: ignore[assignment]

from signfinder.anchors.models import SignMatch, TextAnchor
from signfinder.pdf.parser import ParsedDocument


# ── JSON (v1.1) ───────────────────────────────────────────────────────────────

def parse_parties_json(json_data: dict, language: str | None = None) -> list[dict]:
    """Парсит parties.json → [{name, aliases, patterns, notes, display}]."""
    result = []
    lang = (language or "").lower()[:2]

    for party_name, party_data in json_data.get("parties", {}).items():
        langs = party_data.get("languages", {})

        if lang and lang in langs:
            lang_block = langs[lang]
            aliases = lang_block.get("aliases", [])
            patterns = lang_block.get("patterns", [])
        else:
            aliases = []
            patterns = []
            for lb in langs.values():
                aliases.extend(lb.get("aliases", []))
                patterns.extend(lb.get("patterns", []))

        result.append({
            "name": party_name,
            "display": party_data.get("display", party_name),
            "aliases": aliases,
            "patterns": patterns,
            "notes": party_data.get("notes", ""),
        })

    return result


def parse_parties_md(md_text: str) -> list[dict]:
    """Парсит старый parties.md → [{name, aliases, patterns, notes}]."""
    parties = []
    current = None
    mode = None

    for raw in md_text.split("\n"):
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("## СТОРОНА:"):
            if current:
                parties.append(current)
            name = stripped.replace("## СТОРОНА:", "").strip()
            current = {"name": name, "aliases": [], "patterns": [], "notes": ""}
            mode = None
        elif stripped == "aliases:":
            mode = "aliases"
        elif stripped == "sign_patterns:":
            mode = "patterns"
        elif stripped.startswith("notes:"):
            mode = None
            note = stripped[len("notes:"):].strip().strip('"').strip("'")
            if current:
                current["notes"] = note
        elif stripped.startswith("-") and mode and current:
            value = stripped[1:].strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            current[mode].append(value)
        elif stripped == "---":
            if current:
                parties.append(current)
                current = None
            mode = None

    if current:
        parties.append(current)
    return parties


# ── Bbox helpers (v1.3) ──────────────────────────────────────────────────────

_SAME_LINE_TOLERANCE_PT = 4.0
MAX_BBOX_HEIGHT_PT = 60.0
SAME_ROW_Y_TOLERANCE_PT = 6.0


def _extract_anchor_words(matched_text: str) -> list[str]:
    cleaned = re.sub(r"_{2,}", " ", matched_text)
    words = re.findall(r"[\w\u0400-\u04FF]+", cleaned, flags=re.UNICODE)
    seen = set()
    result = []
    for w in words:
        if len(w) >= 3 and w.lower() not in seen:
            seen.add(w.lower())
            result.append(w)
    return result


def _on_same_line(rect_a, rect_b) -> bool:
    center_a = (rect_a.y0 + rect_a.y1) / 2
    center_b = (rect_b.y0 + rect_b.y1) / 2
    return abs(center_a - center_b) <= _SAME_LINE_TOLERANCE_PT


def _merge_rects(rect_a, rect_b):
    return fitz.Rect(
        min(rect_a.x0, rect_b.x0),
        min(rect_a.y0, rect_b.y0),
        max(rect_a.x1, rect_b.x1),
        max(rect_a.y1, rect_b.y1),
    )


def _find_signature_bbox(page, matched_text: str) -> list:
    """Возвращает список bbox для матча regex'а."""
    rects = page.search_for(matched_text)
    if rects:
        return rects

    has_underline = "___" in matched_text or "__" in matched_text
    anchor_words = _extract_anchor_words(matched_text)

    if has_underline and not anchor_words:
        line_rects = page.search_for("___")
        return line_rects[:1] if line_rects else []

    if not has_underline and anchor_words:
        return page.search_for(anchor_words[0])[:1]

    if has_underline and anchor_words:
        anchor_rects = []
        for w in anchor_words:
            found = page.search_for(w)
            if found:
                anchor_rects.extend(found)

        line_rects = page.search_for("___")

        if not anchor_rects:
            return line_rects[:1] if line_rects else []

        if not line_rects:
            return anchor_rects[:1]

        seen_keys = set()
        result = []
        for a in anchor_rects:
            same_line_lines = [u for u in line_rects if _on_same_line(a, u)]
            if not same_line_lines:
                continue

            def dist_to_anchor(u):
                if u.x0 >= a.x1:
                    return u.x0 - a.x1
                if u.x1 <= a.x0:
                    return a.x0 - u.x1
                return 0.0

            nearest = min(same_line_lines, key=dist_to_anchor)
            merged = _merge_rects(a, nearest)

            key = (round(merged.x0, 1), round(merged.y0, 1),
                   round(merged.x1, 1), round(merged.y1, 1))
            if key not in seen_keys:
                seen_keys.add(key)
                result.append(merged)

        if result:
            return result

    if anchor_words:
        return page.search_for(anchor_words[0])[:1]
    return []


_DISQUALIFYING_ROLE_WORDS = {
    "руководитель", "руководителя", "руководителю", "руководителем",
    "начальник", "начальника", "начальнику", "начальником",
    "сотрудник", "сотрудника", "сотруднику", "сотрудником",
    "специалист", "специалиста", "специалисту", "специалистом",
    "менеджер", "менеджера", "менеджеру", "менеджером",
    "координатор", "координатора", "координатору",
    "работник", "работника", "работнику", "работником",
    "помощник", "помощника", "помощнику",
    "глава", "главы", "главу", "главой",
    "зам", "заместитель", "заместителя", "заместителю",
    "ответственный", "ответственного", "ответственному",
    "должность", "должности", "должностью",
    "представители", "представителя", "представителю", "представителем",
    "служба", "службы", "службу", "службе", "службой",
}

_ALIAS_TOKEN_STOP = {
    "общество", "ограниченной", "ответственностью", "компания", "корпорация",
    "генеральный", "директор", "лице", "именуем", "именуемая", "именуемое",
    "именуемый", "далее", "стороны", "стороне", "договор", "договору",
    "ооо", "оао", "зао", "ао", "ип", "пао", "не", "указано", "указана",
    "физическое", "юридическое", "лицо",
}


def _alias_tokens(alias: str) -> list[str]:
    if not alias:
        return []
    cleaned = re.sub(r"[«»\"'()/\\]", " ", alias)
    raw = re.findall(r"[\w\u0400-\u04FF]+", cleaned, flags=re.UNICODE)
    result: list[str] = []
    seen: set[str] = set()
    for t in raw:
        if len(t) < 3:
            continue
        tl = t.lower()
        if tl in _ALIAS_TOKEN_STOP or tl in seen:
            continue
        seen.add(tl)
        result.append(t)
    return result


def _is_alias_in_role_phrase(
    pre_context: str,
    matched_text: str,
    alias_tokens: list[str],
) -> bool:
    if not alias_tokens:
        return False

    combined = (pre_context + " " + matched_text).lower()

    for token in alias_tokens:
        token_lower = token.lower()
        if len(token_lower) < 3:
            continue
        for hit in re.finditer(re.escape(token_lower), combined):
            preceder = combined[max(0, hit.start() - 30):hit.start()]
            words = re.findall(r"[а-яёa-z]+", preceder, flags=re.UNICODE)
            for w in words[-3:]:
                if w in _DISQUALIFYING_ROLE_WORDS:
                    return True
    return False


# ── Поиск ────────────────────────────────────────────────────────────────────

def _has_real_signature_line(text: str) -> bool:
    return bool(re.search(r"_{3,}|\.{5,}", text))


def _bbox_overlap_ratio(a, b) -> float:
    ix0 = max(a.x0, b.x0); iy0 = max(a.y0, b.y0)
    ix1 = min(a.x1, b.x1); iy1 = min(a.y1, b.y1)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    area_a = (a.x1 - a.x0) * (a.y1 - a.y0)
    area_b = (b.x1 - b.x0) * (b.y1 - b.y0)
    smaller = min(area_a, area_b)
    return inter / smaller if smaller > 0 else 0.0


def _bbox_contains_signature_line(page, match_rect) -> bool:
    """СТРОГИЙ критерий: bbox матча пересекается с линией ___ или .... на странице."""
    line_rects = list(page.search_for("___"))
    line_rects.extend(page.search_for("....."))

    if not line_rects:
        return False

    for line in line_rects:
        if (line.y0 <= match_rect.y1 and line.y1 >= match_rect.y0 and
                line.x0 <= match_rect.x1 and line.x1 >= match_rect.x0):
            return True
    return False


def _filter_by_dominant_patterns(
    matches: list[SignMatch],
    total_pages: int = 0,
    min_pages: int = 2,
) -> list[SignMatch]:
    """v1.8.2: ОТКЛЮЧЕН (no-op).

    Директива «один подписант → один паттерн» НЕВЕРНА для RU-договоров
    с инициалированием. Сигнатура сохранена для совместимости.
    """
    return matches


def find_signatures(doc: ParsedDocument, party: dict) -> list[SignMatch]:
    """Поиск мест подписи для заданной стороны."""
    raw_matches: list[SignMatch] = []
    counter = 0

    compiled = []
    for pat in party.get("patterns", []):
        try:
            pat_stripped = re.sub(r'^\(\?:', '', pat)
            is_reverse = (pat_stripped.startswith('_') or
                          pat_stripped.startswith('\\.') or
                          pat_stripped.startswith('.'))
            has_multiline = '\\s\\S' in pat or '\\S\\s' in pat
            if is_reverse and has_multiline:
                continue
            compiled.append((pat, re.compile(pat, re.IGNORECASE | re.UNICODE)))
        except re.error:
            continue

    other_aliases: list[str] = [
        a.strip() for a in party.get("other_aliases", []) if a and len(a.strip()) >= 3
    ]

    our_aliases_all: list[str] = list(party.get("aliases", []) or [])
    if party.get("signer"):
        our_aliases_all.append(party["signer"])
    our_alias_tokens: list[str] = []
    _seen_at: set[str] = set()
    for a in our_aliases_all:
        for t in _alias_tokens(a):
            tl = t.lower()
            if tl not in _seen_at:
                _seen_at.add(tl)
                our_alias_tokens.append(t)

    pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")

    try:
        for page_idx, parsed_page in enumerate(doc.pages):
            text = parsed_page.text
            page = pdf_doc[page_idx]

            page_raw: list[SignMatch] = []
            seen_text_spans: set[tuple] = set()

            for pattern_str, regex in compiled:
                for m in regex.finditer(text):
                    matched_text = m.group(0)

                    if our_alias_tokens:
                        pre_start = max(0, m.start() - 40)
                        pre_ctx = text[pre_start:m.start()]
                        if _is_alias_in_role_phrase(pre_ctx, matched_text, our_alias_tokens):
                            continue

                    if not _has_real_signature_line(matched_text):
                        continue

                    span_key = (m.start(), m.end())
                    if span_key in seen_text_spans:
                        continue
                    seen_text_spans.add(span_key)

                    if other_aliases:
                        if any(alias.lower() in matched_text.lower() for alias in other_aliases):
                            continue

                    rects = _find_signature_bbox(page, matched_text)
                    for rect in rects:
                        if (rect.y1 - rect.y0) > MAX_BBOX_HEIGHT_PT:
                            continue

                        if not _bbox_contains_signature_line(page, rect):
                            continue

                        counter += 1
                        start = max(0, m.start() - 40)
                        end = min(len(text), m.end() + 40)
                        ctx = text[start:end].replace("\n", " ").strip()

                        page_raw.append(SignMatch(
                            id=f"sig_{counter:03d}",
                            page=page_idx,
                            bbox=tuple(rect),
                            context=ctx,
                            party=party["name"],
                            pattern=pattern_str,
                        ))

            # Дедуп overlap
            deduped: list[SignMatch] = []
            for candidate in page_raw:
                c_rect = fitz.Rect(candidate.bbox)
                is_dup = False
                for kept in deduped:
                    k_rect = fitz.Rect(kept.bbox)
                    if _bbox_overlap_ratio(c_rect, k_rect) > 0.70:
                        is_dup = True
                        break
                if not is_dup:
                    deduped.append(candidate)

            # Дедуп row
            def _area(b):
                return (b[2] - b[0]) * (b[3] - b[1])

            def _y_center(b):
                return (b[1] + b[3]) / 2

            def _x_overlap(a, b):
                return min(a[2], b[2]) > max(a[0], b[0])

            row_deduped: list[SignMatch] = []
            for candidate in sorted(deduped, key=lambda m: _area(m.bbox)):
                c_yc = _y_center(candidate.bbox)
                is_dup = False
                for kept in row_deduped:
                    if (abs(c_yc - _y_center(kept.bbox)) <= SAME_ROW_Y_TOLERANCE_PT and
                            _x_overlap(candidate.bbox, kept.bbox)):
                        is_dup = True
                        break
                if not is_dup:
                    row_deduped.append(candidate)

            raw_matches.extend(row_deduped)
    finally:
        pdf_doc.close()

    raw_matches = _filter_by_dominant_patterns(
        raw_matches, total_pages=len(doc.pages), min_pages=2,
    )

    return raw_matches


def find_signatures_smart(
    doc: ParsedDocument,
    party: dict,
    min_expected: int = 1,
    llm_fallback: bool = False,
    llm_finder_fn=None,
) -> tuple[list[SignMatch], str]:
    """find_signatures + source label.

    Параметры:
        llm_finder_fn — опциональный callable(doc, party_name, language) → dict
                        с ключом "patterns" для LLM-fallback. Если None —
                        fallback не выполняется (даже при llm_fallback=True).
                        Это убирает прямую зависимость от pattern_extractor.

    Returns:
        (matches, source) где source ∈ {"regex", "llm_fallback"}
    """
    matches = find_signatures(doc, party)

    if matches or not llm_fallback or llm_finder_fn is None:
        return matches, "regex"

    try:
        result = llm_finder_fn(doc, party["name"], getattr(doc, "language", "ru"))
        if result.get("patterns"):
            fallback_party = dict(party)
            existing = fallback_party.get("patterns", [])
            fallback_party["patterns"] = list(dict.fromkeys(
                existing + result["patterns"]
            ))
            matches = find_signatures(doc, fallback_party)
            return matches, "llm_fallback"
    except Exception as e:
        sys.stderr.write(f"[finder] llm_fallback error: {e}\n")

    return [], "regex"


# ── Якорный API (v1.7) ────────────────────────────────────────────────────────

def apply_template_anchors(doc, template) -> list[SignMatch]:
    """Применяет якоря шаблона к новому документу.

    v1.8.3: bbox-fallback для всех типов якорей если regex не нашёл совпадений.
    """
    matches: list[SignMatch] = []
    counter = 0

    pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")
    try:
        anchors = template.anchors or []
        for raw_anchor in anchors:
            if isinstance(raw_anchor, dict):
                anchor = TextAnchor(**raw_anchor)
            else:
                anchor = raw_anchor

            pattern_str = anchor.generated_pattern
            regex = None
            if pattern_str:
                try:
                    regex = re.compile(pattern_str, re.IGNORECASE | re.UNICODE)
                except re.error:
                    sys.stderr.write(f"[finder] bad anchor pattern: {pattern_str}\n")
                    regex = None

            if anchor.page_hint == "first":
                page_range = [0]
            elif anchor.page_hint == "last":
                page_range = [len(doc.pages) - 1]
            elif anchor.page_hint == "any":
                page_range = list(range(len(doc.pages)))
            else:
                try:
                    page_range = [int(anchor.page_hint)]
                except (ValueError, TypeError):
                    page_range = list(range(len(doc.pages)))

            anchor_match_count = 0

            if regex is not None:
                for page_idx in page_range:
                    if page_idx >= len(doc.pages):
                        continue
                    text = doc.pages[page_idx].text
                    page = pdf_doc[page_idx]

                    for m in regex.finditer(text):
                        matched_text = m.group(0)

                        if anchor.context_before:
                            ctx_norm = re.sub(r"\s+", " ", anchor.context_before).strip().lower()
                            if ctx_norm:
                                ctx_start = max(0, m.start() - len(anchor.context_before) - 40)
                                preceding_raw = text[ctx_start:m.start()]
                                preceding_norm = re.sub(r"\s+", " ", preceding_raw).lower()
                                if ctx_norm not in preceding_norm:
                                    continue

                        rects = _find_signature_bbox(page, matched_text)
                        for rect in rects:
                            if (rect.y1 - rect.y0) > MAX_BBOX_HEIGHT_PT:
                                continue
                            counter += 1
                            start = max(0, m.start() - 40)
                            end = min(len(text), m.end() + 40)
                            ctx = text[start:end].replace("\n", " ").strip()

                            matches.append(SignMatch(
                                id=f"tpl_{counter:03d}",
                                page=page_idx,
                                bbox=tuple(rect),
                                context=ctx,
                                party=getattr(template, "name", "template"),
                                pattern=pattern_str,
                            ))
                            anchor_match_count += 1

            # bbox-fallback
            if anchor_match_count == 0:
                fallback_page_idx = page_range[0] if page_range else 0
                if (0 <= fallback_page_idx < len(doc.pages) and
                        anchor.bbox and len(anchor.bbox) == 4):
                    counter += 1
                    matches.append(SignMatch(
                        id=f"tpl_{counter:03d}",
                        page=fallback_page_idx,
                        bbox=tuple(anchor.bbox),
                        context=(anchor.anchor_text or "")[:80],
                        party=getattr(template, "name", "template"),
                        pattern=f"[bbox_fallback] {pattern_str or ''}",
                    ))
                    sys.stderr.write(
                        f"[finder] bbox-fallback anchor source={getattr(anchor, 'added_by', '?')} "
                        f"page={fallback_page_idx} text={(anchor.anchor_text or '')[:40]!r}\n"
                    )
    except Exception as e:
        sys.stderr.write(f"[finder] apply_template_anchors: {e}\n")
    finally:
        pdf_doc.close()

    return matches


def regex_match_to_anchor(match: SignMatch, page_idx: int, language: str) -> TextAnchor:
    """Конвертирует SignMatch → TextAnchor с added_by='auto_regex'."""
    from signfinder.anchors.builder import build_anchor_from_regex_match

    bbox = match.bbox if isinstance(match.bbox, tuple) else tuple(match.bbox)
    ctx = match.context or ""
    pattern_str = match.pattern or ""
    try:
        m = re.search(pattern_str, ctx, re.IGNORECASE | re.UNICODE)
        if m:
            ctx_before = ctx[:m.start()]
            ctx_after = ctx[m.end():]
        else:
            ctx_before, ctx_after = "", ""
    except Exception:
        ctx_before, ctx_after = "", ""

    return build_anchor_from_regex_match(
        pattern=match.pattern,
        match_text=match.context,
        match_bbox=bbox,
        page_idx=page_idx,
        language=language,
        context_before=ctx_before,
        context_after=ctx_after,
    )
