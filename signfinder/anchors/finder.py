"""Поиск мест подписи в распарсенном документе.

FIX v1.9.2: apply_template_anchors — при конструировании TextAnchor из dict
добавляем дефолты для полей отсутствующих в старых шаблонах
(anchor_type, offset_pt, context_before, context_after, added_at).
Streamlit сохраняет только 8 полей (_anchor_to_dict), TextAnchor требует 12.
Без фикса apply_template_anchors падал с TypeError и возвращал [],
из-за чего applied_template=None даже при green score=1.0.
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


# ── Дефолты для полей TextAnchor отсутствующих в старых шаблонах ─────────────
_ANCHOR_DEFAULTS = {
    "anchor_type": "text_proximity",
    "offset_pt": 0.0,
    "context_before": "",
    "context_after": "",
    "added_at": "",
}


def _make_text_anchor(raw: dict) -> TextAnchor:
    """TextAnchor из dict с дефолтами для отсутствующих полей."""
    data = {**_ANCHOR_DEFAULTS, **raw}
    return TextAnchor(**data)


# ── JSON (v1.1) ───────────────────────────────────────────────────────────────

def parse_parties_json(json_data: dict, language: str | None = None) -> list[dict]:
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


# ── Bbox helpers ──────────────────────────────────────────────────────────────

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
        min(rect_a.x0, rect_b.x0), min(rect_a.y0, rect_b.y0),
        max(rect_a.x1, rect_b.x1), max(rect_a.y1, rect_b.y1),
    )


def _expand_line_bbox(match_bbox, page_words, tolerance_y: float = 3.0, max_gap: float = 20.0):
    """Расширить bbox до НЕПРЕРЫВНОЙ строки (для точечных/подчёркнутых линий).

    PyMuPDF дробит длинную линию '.....' / '_____' на несколько 'слов'; первое
    может быть шириной ~3pt → подпись садится в узкий прямоугольник. Соединяем
    соседние слова на той же y, но НЕ перепрыгиваем большие разрывы (max_gap) —
    защита от склейки колонок в dual_column документах через гутер.
    """
    x0, y0, x1, y1 = match_bbox
    y_center = (y0 + y1) / 2
    same_line = sorted(
        (w for w in page_words
         if abs((w.bbox[1] + w.bbox[3]) / 2 - y_center) <= tolerance_y),
        key=lambda w: w.bbox[0],
    )
    if not same_line:
        return [x0, y0, x1, y1]

    nx0, ny0, nx1, ny1 = x0, y0, x1, y1
    for w in same_line:
        wx0, wy0, wx1, wy1 = w.bbox
        # Поглощаем слово только если оно примыкает к текущему боксу (непрерывный
        # ряд), не перепрыгивая через разрыв шире max_gap.
        if wx0 <= nx1 + max_gap and wx1 >= nx0 - max_gap:
            nx0 = min(nx0, wx0); ny0 = min(ny0, wy0)
            nx1 = max(nx1, wx1); ny1 = max(ny1, wy1)
    return [nx0, ny0, nx1, ny1]


def _find_signature_bbox(page, matched_text: str) -> list:
    rects = page.search_for(matched_text)
    if rects:
        return rects
    # has_underline: underscores OR 5+ consecutive dots (dot-line signature)
    has_underline = bool(re.search(r'_{2,}|\.{5,}', matched_text))
    anchor_words = _extract_anchor_words(matched_text)

    def _get_line_rects():
        lr = page.search_for("___")
        if not lr:
            lr = page.search_for(".....")
        return lr

    if has_underline and not anchor_words:
        line_rects = _get_line_rects()
        return line_rects[:1] if line_rects else []
    if not has_underline and anchor_words:
        return page.search_for(anchor_words[0])[:1]
    if has_underline and anchor_words:
        anchor_rects = []
        for w in anchor_words:
            found = page.search_for(w)
            if found:
                anchor_rects.extend(found)
        line_rects = _get_line_rects()
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
                if u.x0 >= a.x1: return u.x0 - a.x1
                if u.x1 <= a.x0: return a.x0 - u.x1
                return 0.0
            nearest = min(same_line_lines, key=dist_to_anchor)
            # Clip line to anchor's x-column: prevents full-width rect in dual-column docs
            if a.x0 > nearest.x0 + 50:
                nearest = fitz.Rect(a.x0 - 5, nearest.y0, nearest.x1, nearest.y1)
            merged = _merge_rects(a, nearest)
            key = (round(merged.x0, 1), round(merged.y0, 1),
                   round(merged.x1, 1), round(merged.y1, 1))
            if key not in seen_keys:
                seen_keys.add(key)
                result.append(merged)
        # Column-proximity fallback: reverse patterns where line is on different row than
        # role/company text (e.g. ".......↵Innowise"). Find nearest line in anchor's column.
        if not result:
            for a in anchor_rects:
                col_lines_clipped = []
                for cl in line_rects:
                    if cl.x0 <= a.x0 + 50 and cl.x1 >= a.x0 - 20:
                        clipped = fitz.Rect(max(cl.x0, a.x0 - 10), cl.y0, cl.x1, cl.y1)
                        if not clipped.is_empty:
                            col_lines_clipped.append(clipped)
                if col_lines_clipped:
                    nearest = min(col_lines_clipped,
                                  key=lambda u: abs((u.y0 + u.y1) / 2 - (a.y0 + a.y1) / 2))
                    key = (round(nearest.x0, 1), round(nearest.y0, 1),
                           round(nearest.x1, 1), round(nearest.y1, 1))
                    if key not in seen_keys:
                        seen_keys.add(key)
                        result.append(nearest)
                    break
        if result:
            return result
    if anchor_words:
        return page.search_for(anchor_words[0])[:1]
    return []


MIN_SIG_WIDTH_PT = 12.0  # уже = битый фрагмент линии (PyMuPDF дробит '.....'), не подпись


def _keep_trailing_anchor_column(page, matched_text: str, rects: list) -> list:
    """Оставить rect'ы в колонке ХВОСТОВОГО якоря (названия стороны).

    Для паттернов «линия → название» (reverse-dot `\\.{5,}…Innowise`,
    docusign `\\tN\\…Innowise`) релевантна колонка названия, стоящего в конце
    matched_text. `_extract_anchor_words` возвращает и служебный DocuSign-текст
    ('Place, date', 'vor Versand aufheben' — слева у тега), из-за чего возникает
    ложный rect в чужой колонке. Берём последнее значимое слово (≈ название) и
    отбрасываем rect'ы не из его колонки.
    """
    if len(rects) <= 1:
        return rects
    words = _extract_anchor_words(matched_text)
    if not words:
        return rects
    tail_word = words[-1]
    anchor_rects = page.search_for(tail_word)
    if not anchor_rects:
        return rects
    kept = []
    for r in rects:
        rc = (r.x0 + r.x1) / 2
        ryc = (r.y0 + r.y1) / 2
        for ar in anchor_rects:
            if abs((ar.y0 + ar.y1) / 2 - ryc) > 200:  # только близкие по вертикали
                continue
            x_overlap = min(ar.x1, r.x1) > max(ar.x0, r.x0)
            if x_overlap or abs((ar.x0 + ar.x1) / 2 - rc) < 120:
                kept.append(r)
                break
    return kept or rects


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


def _is_alias_in_role_phrase(pre_context, matched_text, alias_tokens):
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


# ── Поиск ─────────────────────────────────────────────────────────────────────

def _has_real_signature_line(text: str) -> bool:
    return bool(re.search(r"_{3,}|\.{5,}|\\[tse]\d+\\", text))


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
    line_rects = list(page.search_for("___"))
    line_rects.extend(page.search_for("....."))
    if line_rects:
        for line in line_rects:
            if (line.y0 <= match_rect.y1 and line.y1 >= match_rect.y0 and
                    line.x0 <= match_rect.x1 and line.x1 >= match_rect.x0):
                return True
    # DocuSign: подчёркивания — графические элементы, маркеры \t1\ есть только в тексте.
    # Расширяем bbox и ищем \tN\ / \eN\ / \sN\ в тексте страницы вокруг матча.
    try:
        expanded = fitz.Rect(
            match_rect.x0 - 200, match_rect.y0 - 120,
            match_rect.x1 + 200, match_rect.y1 + 120,
        )
        clip_text = page.get_text("text", clip=expanded)
        if re.search(r"\\[tse]\d+\\", clip_text):
            return True
    except Exception:
        pass
    return False


def _filter_by_dominant_patterns(matches, total_pages=0, min_pages=2):
    return matches


def find_signatures(doc: ParsedDocument, party: dict) -> list[SignMatch]:
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

    other_aliases = [a.strip() for a in party.get("other_aliases", []) if a and len(a.strip()) >= 3]
    our_aliases_all = list(party.get("aliases", []) or [])
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
                    # Проверяем подпись-линию в самом тексте ИЛИ в контексте
                    # перед совпадением — это ловит паттерны вида "______ (Имя)",
                    # где подчёркивание идёт ДО имени и сам matched_text его не содержит.
                    pre_ctx_start = max(0, m.start() - 50)
                    pre_ctx = text[pre_ctx_start:m.start()]
                    if not _has_real_signature_line(matched_text + pre_ctx):
                        continue
                    span_key = (m.start(), m.end())
                    if span_key in seen_text_spans:
                        continue
                    seen_text_spans.add(span_key)
                    if other_aliases:
                        if any(alias.lower() in matched_text.lower() for alias in other_aliases):
                            continue
                    rects = _find_signature_bbox(page, matched_text)
                    # Паттерны «линия → название» (reverse-dot, docusign-tab): название
                    # стороны в конце. Отбрасываем rect'ы из чужих колонок (ложный левый
                    # матч от служебного DocuSign-текста 'Place, date / vor Versand aufheben').
                    trailing_anchor = (
                        pattern_str.startswith("\\.")
                        or pattern_str.startswith("\\\\t")
                        or pattern_str.startswith("\\\\s")
                        or pattern_str.startswith("\\\\e")
                    )
                    if trailing_anchor:
                        rects = _keep_trailing_anchor_column(page, matched_text, rects)
                    # Для точечных/подчёркнутых линий PyMuPDF даёт узкий первый
                    # сегмент (~3pt). Расширяем bbox до всей непрерывной линии.
                    expand_line = bool(re.search(r'\\\.\{5|_\{3', pattern_str))
                    for rect in rects:
                        if (rect.y1 - rect.y0) > MAX_BBOX_HEIGHT_PT:
                            continue
                        if not _bbox_contains_signature_line(page, rect):
                            continue
                        if expand_line:
                            rect = fitz.Rect(_expand_line_bbox(
                                (rect.x0, rect.y0, rect.x1, rect.y1),
                                parsed_page.words,
                            ))
                            if (rect.y1 - rect.y0) > MAX_BBOX_HEIGHT_PT:
                                continue
                        # Битый фрагмент линии (~3pt) — не место подписи.
                        if (rect.x1 - rect.x0) < MIN_SIG_WIDTH_PT:
                            continue
                        counter += 1
                        start = max(0, m.start() - 40)
                        end = min(len(text), m.end() + 40)
                        ctx = text[start:end].replace("\n", " ").strip()
                        page_raw.append(SignMatch(
                            id=f"sig_{counter:03d}", page=page_idx,
                            bbox=tuple(rect), context=ctx,
                            party=party["name"], pattern=pattern_str,
                        ))

            deduped: list[SignMatch] = []
            for candidate in page_raw:
                c_rect = fitz.Rect(candidate.bbox)
                is_dup = any(_bbox_overlap_ratio(c_rect, fitz.Rect(k.bbox)) > 0.70 for k in deduped)
                if not is_dup:
                    deduped.append(candidate)

            def _area(b): return (b[2] - b[0]) * (b[3] - b[1])
            def _y_center(b): return (b[1] + b[3]) / 2
            def _x_overlap(a, b): return min(a[2], b[2]) > max(a[0], b[0])

            row_deduped: list[SignMatch] = []
            for candidate in sorted(deduped, key=lambda m: _area(m.bbox)):
                c_yc = _y_center(candidate.bbox)
                is_dup = any(
                    abs(c_yc - _y_center(k.bbox)) <= SAME_ROW_Y_TOLERANCE_PT and
                    _x_overlap(candidate.bbox, k.bbox)
                    for k in row_deduped
                )
                if not is_dup:
                    row_deduped.append(candidate)
            raw_matches.extend(row_deduped)
    finally:
        pdf_doc.close()
    return _filter_by_dominant_patterns(raw_matches, total_pages=len(doc.pages), min_pages=2)


def find_signatures_smart(doc, party, min_expected=1, llm_fallback=False, llm_finder_fn=None):
    matches = find_signatures(doc, party)
    if matches or not llm_fallback or llm_finder_fn is None:
        return matches, "regex"
    try:
        result = llm_finder_fn(doc, party["name"], getattr(doc, "language", "ru"))
        if result.get("patterns"):
            fallback_party = dict(party)
            existing = fallback_party.get("patterns", [])
            fallback_party["patterns"] = list(dict.fromkeys(existing + result["patterns"]))
            matches = find_signatures(doc, fallback_party)
            return matches, "llm_fallback"
    except Exception as e:
        sys.stderr.write(f"[finder] llm_fallback error: {e}\n")
    return [], "regex"


# ── Якорный API ───────────────────────────────────────────────────────────────

def apply_template_anchors(doc, template) -> list[SignMatch]:
    """Применяет якоря шаблона к новому документу.

    FIX v1.9.2: _make_text_anchor добавляет дефолты для полей отсутствующих
    в старых шаблонах. Раньше TextAnchor(**raw_anchor) падал с TypeError
    и вся функция возвращала [] через except.
    """
    matches: list[SignMatch] = []
    counter = 0
    pdf_doc = fitz.open(stream=doc.pdf_bytes, filetype="pdf")
    try:
        anchors = template.anchors or []
        for raw_anchor in anchors:
            try:
                if isinstance(raw_anchor, dict):
                    anchor = _make_text_anchor(raw_anchor)
                else:
                    anchor = raw_anchor
            except Exception as e:
                sys.stderr.write(f"[finder] skip bad anchor: {e} raw={raw_anchor}\n")
                continue

            pattern_str = anchor.generated_pattern

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

            # v1.18.2 FIX: ручные якоря (manual_click) несут точный bbox, выбранный
            # оператором кликом. Повторный прогон generated_pattern перенёс бы подпись
            # на другую подчёркнутую линию (паттерн матчит несколько мест на странице).
            # Берём сохранённый bbox напрямую — ровно туда, куда поставил оператор.
            if getattr(anchor, "added_by", "") == "manual_click":
                bbox = anchor.bbox
                fb_page = page_range[0] if page_range else 0
                if (isinstance(bbox, (list, tuple)) and len(bbox) == 4
                        and 0 <= fb_page < len(doc.pages)):
                    counter += 1
                    matches.append(SignMatch(
                        id=f"tpl_{counter:03d}",
                        page=fb_page,
                        bbox=tuple(bbox),
                        context=(anchor.anchor_text or "")[:120],
                        party=getattr(template, "name", "template"),
                        pattern=pattern_str or "",
                        added_by="manual_click",
                    ))
                    sys.stderr.write(
                        f"[finder] manual-anchor direct bbox page={fb_page} "
                        f"text={repr((anchor.anchor_text or '')[:40])}\n"
                    )
                continue

            regex = None
            if pattern_str:
                try:
                    regex = re.compile(pattern_str, re.IGNORECASE | re.UNICODE)
                except re.error:
                    sys.stderr.write(f"[finder] bad anchor pattern: {pattern_str}\n")

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
                                preceding_norm = re.sub(r"\s+", " ", text[ctx_start:m.start()]).lower()
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
                                id=f"tpl_{counter:03d}", page=page_idx,
                                bbox=tuple(rect), context=ctx,
                                party=getattr(template, "name", "template"),
                                pattern=pattern_str,
                            ))
                            anchor_match_count += 1

            # bbox-fallback для любых якорей (включая manual_click)
            if anchor_match_count == 0:
                fallback_page_idx = page_range[0] if page_range else 0
                bbox = anchor.bbox
                if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
                    if 0 <= fallback_page_idx < len(doc.pages):
                        counter += 1
                        matches.append(SignMatch(
                            id=f"tpl_{counter:03d}",
                            page=fallback_page_idx,
                            bbox=tuple(bbox),
                            # Сохраняем оригинальный паттерн — он нужен _find_underscore_anchor
                            # для определения стороны (начало с '_' → x0+offset).
                            # Флаг bbox_fallback кладём в context, а не в pattern.
                            context=f"[bbox_fallback] {(anchor.anchor_text or '')[:80]}",
                            party=getattr(template, "name", "template"),
                            pattern=pattern_str or "",
                        ))
                        sys.stderr.write(
                            f"[finder] bbox-fallback added_by={getattr(anchor,'added_by','?')} "
                            f"page={fallback_page_idx} "
                            f"text={repr((anchor.anchor_text or '')[:40])}\n"
                        )
    except Exception as e:
        sys.stderr.write(f"[finder] apply_template_anchors fatal: {e}\n")
    finally:
        pdf_doc.close()
    return matches


def manual_match_to_anchor(match: SignMatch, page_idx: int) -> TextAnchor:
    """SignMatch ручного якоря → TextAnchor с сохранением added_by='manual_click'.

    v1.18.3: без этого провенанс терялся (regex_match_to_anchor хардкодит
    'auto_regex'), и при повторном сохранении/загрузке шаблона ручной якорь
    деградировал в auto → подпись «уезжала» на regex-линию. Сохраняем точный
    bbox оператора и флаг manual_click — стабильно через любое число циклов.
    """
    from datetime import datetime, timezone
    from uuid import uuid4
    bbox = match.bbox if isinstance(match.bbox, tuple) else tuple(match.bbox)
    return TextAnchor(
        id=uuid4().hex,
        anchor_type="text_proximity",
        anchor_level=1,
        anchor_text=(match.context or "").strip()[:120],
        position="on",
        offset_pt=0.0,
        generated_pattern=match.pattern or "",
        context_before="",
        context_after="",
        page_hint=str(page_idx),
        added_by="manual_click",
        added_at=datetime.now(timezone.utc).isoformat(),
        bbox=bbox,
    )


def regex_match_to_anchor(match: SignMatch, page_idx: int, language: str) -> TextAnchor:
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
        pattern=match.pattern, match_text=match.context, match_bbox=bbox,
        page_idx=page_idx, language=language,
        context_before=ctx_before, context_after=ctx_after,
    )
