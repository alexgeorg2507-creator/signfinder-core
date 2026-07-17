"""SignFinder — core engine for automatic signature placement in contracts.

v1.20.17 (cosmetic, reported directly against a live signed PDF): auto-placed
signature visibly larger than a manually-placed one left at the cabinet's own
default (unresized) box.
  - pdf/overlay.py: DEFAULT_SIGNATURE_HEIGHT_PT 42pt -> 28pt. Root cause:
    the manual placer's default box (130x54.6 CSS px, SignfinderLand's
    _onPagePlaceClick) goes through page.insert_image(..., keep_proportion=
    True) in apply_signature, so a signature PNG wider than the box's own
    ~2.4 aspect ratio gets fit BY WIDTH, shrinking the effective height well
    below the box's own 54.6px-converted height. The auto path had no such
    constraint — sig_w = sig_h * aspect, uncapped — so it always rendered at
    the full 42pt regardless of how wide that made it. 28pt is a first
    evidence-based estimate from a real side-by-side screenshot, not a
    derived invariant: the manual default's effective PDF-point size
    depends on browser viewport width (canvas._pdfScale) and the specific
    signature's aspect ratio, so no single constant matches it exactly at
    every window size — same iterate-on-real-screenshots approach already
    used for the descender constant (v1.20.7 -> v1.20.9 -> v1.20.10).
  - Position/descender untouched per explicit instruction — this is a size-
    only change.

v1.20.16 (Fix-14.1, generalized after a second real document hit the same
failure mode through DIFFERENT patterns — not just _add_reverse_underscore_patterns'
own):
  - Second document, same symptom (footer signature found only on the
    requisites page, not on the per-page "Клиент____ Представитель____"
    footer). pipeline_debug showed the new v1.20.15 insurance layer DID work
    correctly on its own patterns (12 matches -> 8 after
    _verify_reverse_underscore_matches) — but the *existing*
    _filter_by_our_side_context then dropped 8 -> 2 -> 1, because several of
    run_step4's own LLM-generated patterns for this document
    (`Клиент[\\s]{0,5}_{3,}`, `_{3,}\\s*Клиент`, etc.) hit the exact same
    footer, and those patterns weren't in trusted_patterns, so they went
    through the OLD text-offset-based context check — which fails for the
    same structural reason v1.20.15 already diagnosed for its own patterns:
    this document's footer is ALSO extracted before the page's body text
    (visible directly in pipeline_debug.prompt_step4's "ПЕРВАЯ СТРАНИЦА"
    excerpt, which starts with the footer line, not the document title).
  - Confirms the v1.20.15 fix was correct but too narrow — the underlying
    problem (text offset position != visual position for this document
    class) affects ANY pattern that matches inside a local_dual_zone, not
    just the two deterministic ones added there. Patching trusted_patterns
    per new pattern source would be whack-a-mole; the user explicitly asked
    for a general fix, not another document/pattern-specific patch.
  - pipeline/auto1.py: _filter_by_our_side_context reworked for the
    local_dual_zone case (not dual_column_vertical whole-page, left alone —
    a different scenario, no evidence it's broken). Generalizes v1.20.15's
    own technique: page.get_textbox(match.bbox) — does the match's own
    (already _expand_line_bbox-expanded) rectangle actually contain one of
    our synonyms? Position-based, so it doesn't care what extraction order
    produced page.text. Falls back to the old text-offset check only if the
    geometric read itself fails (exception). Signature changed from
    `doc_pages: list` to `doc: ParsedDocument` (needed for doc.pdf_bytes to
    open the page for get_textbox) — one call site, updated.
  - reverse_underscore_pats stay in trusted_patterns (bypass this filter
    entirely) since _verify_reverse_underscore_matches already ran the same
    geometric check on them — avoids redundant PDF reads, not a correctness
    requirement.
  - test_our_side_filter.py rewritten for the new signature; added a test
    that deliberately feeds a page.text with zero relationship to the real
    content, proving the local_dual_zone path no longer depends on it.
  - Re-verified end-to-end against the original v1.20.15 document
    (signed_1ДоговорЛебедев.pdf) to confirm no regression: still 10 raw -> 5
    correct, stable across repeated runs. Full suite: 154 passed.

v1.20.15 (Fix-14, deterministic footer coverage — redesigned from the task's
own draft after its cited evidence turned out to contradict it):
  - pipeline/auto1.py: _add_reverse_underscore_patterns() + a mandatory
    companion _verify_reverse_underscore_matches(), both new. Same problem
    _add_reverse_dot_patterns solves for dotted lines (\\.{5,}) — a
    deterministic insurance layer for underscored footer lines like
    "Заказчик____  Подрядчик____", since run_step4 (LLM) doesn't reliably
    generate this pattern shape on its own (confirmed by comparing two real
    analyze() runs on the same document: one got lucky, one didn't).
  - The task's own draft proposed `_{3,}[^\\n]{0,50}X` (single-line,
    mirroring _add_reverse_dot_patterns' shape) and cited
    debug_fix8_final_patterns.json as evidence it works. Re-read that exact
    file before implementing: it documents the opposite — that pattern was
    present in an LLM run's final_patterns but produced zero matches, with
    an explicit note "не матчит — гипотеза кодера про [^\\n] vs [\\s\\S]
    проверяется этим скриптом". Confirmed why: on this document's footer,
    the underscore run and the role word are on different PyMuPDF-extracted
    lines even at generous budgets — [^\\n] structurally cannot cross that
    boundary, at any width.
  - Tried the cross-line fix instead (`_{3,}[\\s\\S]{0,N}X`, consuming).
    Empirically works for finding the right start position once N>=100, but
    _expand_line_bbox then merges the match's now-multi-line matched_text
    into a bbox spanning the ENTIRE footer width (both parties' columns) —
    unusable for signature placement.
  - Landed on a non-consuming lookahead instead: `_{3,}(?=[\\s\\S]{0,150}X)`.
    matched_text stays just the underscore run (single line), so
    _expand_line_bbox only merges the contiguous same-column content (role
    word + its own underscores) — clean, correctly-sized bbox. Trade-off:
    since the lookahead doesn't consume, finditer finds the underscore run
    on BOTH sides of the footer (each independently sees the target role
    within budget) — hence _verify_reverse_underscore_matches: a mandatory
    follow-up pass that opens the page and checks with
    page.get_textbox(match.bbox) whether the role/legal_entity text is
    actually inside the match's own (already _expand_line_bbox-expanded)
    rectangle, dropping it otherwise. Proven on the real document: 10 raw
    matches (5 correct + 5 wrong-party) -> 5 correct, deterministic across 3
    repeated runs (pure regex, no LLM in this layer).
  - Deliberately does NOT route these matches through
    _filter_by_our_side_context's existing text-context check — traced why
    it can't work here anyway: on this document the footer is PDF content
    block 0 (extracted before any body text), so "80 characters of text
    before the match" has no relationship to "content visually above it on
    the page". These patterns are added to the filter's trusted_patterns
    set instead (same mechanism already used for signer_pats), since
    get_textbox is already a strictly more precise check for this case.
  - New debug fields: reverse_underscore_patterns_added,
    reverse_underscore_verify {before, after}.
  - 12 new tests (test_reverse_underscore_patterns.py). Full suite: 153
    passed.

v1.20.14 (Fix-12.3, text-anchored manual placements):
  - anchors/finder.py: apply_template_anchors's manual_click branch now tries
    a text-search-first reapply before falling back to the frozen absolute
    bbox recorded at click time. The cabinet's manual-placement UI
    (SignfinderLand) now probes the nearest text landmark on confirm (new
    signfinder-api endpoint POST /v1/me/manual-anchor/probe) and remembers
    {anchor_bbox, offset_dx, offset_dy} in TextAnchor.context_before as JSON
    (reusing an existing field that manual_click anchors never populated
    otherwise — no schema migration). On reapply: search_for(anchor_text) on
    the new document's page, pick the occurrence closest to the original
    anchor_bbox position if the text repeats, then place at
    found.x0+offset_dx / found.y0+offset_dy with the ORIGINAL drawn
    width/height (not recomputed). Falls back to the old frozen-bbox
    behavior whenever context_before is empty/unparseable (templates saved
    before this change) or the text isn't found at all (radically different
    document) — same fallback signfinder-core/pdf/overlay.py's manual bypass
    already relies on for bbox shape. 5 new tests
    (tests/test_manual_anchor_reapply.py): reflow-follows-text, not-found
    fallback, old-template fallback, malformed-JSON fallback,
    multi-occurrence disambiguation by proximity to the original position.

v1.20.13 (Fix-10.4, reported after Fix-10.3 confirmed fixed: "manual signature's
size/position aren't saved in the template"):
  - __init__.py: SignFinder._to_match() — converts a TextAnchor into a SignMatch
    for sign(); built the SignMatch without passing added_by, so it silently
    fell back to SignMatch's default "auto_regex". Every manual_click anchor
    that reaches sign() as a TextAnchor (i.e. anything except the very first
    live placement, which arrives pre-built as a SignMatch via
    manual_anchors_json and skips _to_match entirely — a green template match
    reapplying a remembered manual anchor, or a plain resign after reload)
    got downgraded to auto_regex. apply_signature()'s manual_exact/manual_click
    bypass (overlay.py) then didn't fire, so it fell through to the text-search
    placement branch with pattern="" (manual anchors' generated_pattern is
    always empty) — losing both the exact bbox (position) and the drawn
    width/height (size, since that branch computes sig_h/sig_w from
    DEFAULT_SIGNATURE_HEIGHT_PT*scale, not from the anchor's own bbox).
    manual_match_to_anchor() (anchors/finder.py, v1.18.3) already carries
    added_by through the opposite direction (SignMatch -> TextAnchor) with a
    docstring warning about exactly this risk — _to_match was the direction
    that got missed. One-line fix: pass added_by=item.added_by through.

v1.20.12 (Fix-10.3, diagnosed from a real production analyze() response showing
10 anchors instead of 5, signature on both Заказчик AND Подрядчик footer lines):
  - pdf/parser.py: new _detect_local_dual_zones() + ParsedPage.dual_zones.
    _detect_gutter() scans the WHOLE page's words for one corridor with <=2
    crossings — on a page dominated by full-width body paragraphs, a narrow
    two-block footer line ("Заказчик____   Подрядчик____") is invisible to it
    (body words swamp every candidate cut), so doc.layout comes out
    single_column even though that one footer line is genuinely two-sided.
    Verified directly against signed_1ДоговорЛебедев.pdf's real word
    coordinates: _detect_gutter finds nothing on any page, but the footer row
    (y=766.9-781.2) has a clean 152pt gap centered at 47% width. The new
    function clusters words into rows by vertical bbox overlap (catches the
    case where the underscore run and the role label are separate MuPDF
    'line's despite occupying the same visual row) and looks for a
    per-row gap >=8% page width centered in the 25-75% band.
  - pipeline/auto1.py: _filter_by_our_side_context gate no longer requires
    doc.layout=="dual_column_vertical" alone — it also fires when any page
    has local dual_zones. The function itself is now zone-aware: a match is
    only subject to the our-side context check if it's on a fully
    dual_column_vertical page (old behavior, unchanged) OR its bbox falls
    inside one of that page's dual_zones (+-4pt margin). Matches outside any
    zone pass through untouched, same as before on ordinary single_column
    pages — this only widens WHERE the existing, already-proven context
    check is allowed to run, it doesn't change what it does.
  - Root cause of the regression: an LLM-generated reverse pattern like
    '_{3,}[\\s\\S]{0,50}Заказчик' is ambiguous on this footer — Заказчик's own
    underscore run is followed by a huge run of trailing spaces (~95 chars)
    before reaching the word "Заказчик", which overflows the {0,50} budget,
    so the only regex match that budget-fits starts at Подрядчик's own
    underscore run instead (one \\n away from "Заказчик"). Without the
    our-side filter (skipped — single_column), that false match survives
    alongside a correct one and both get saved into the remembered template,
    reproducing on every future match. This was not a Fix-10.1 bug — remember
    faithfully persisted whatever _workAnchors already contained from the
    original analyze() call.

v1.20.11 (Fix-10.1):
  - pdf/overlay.py: the freeform-bbox placement bypass now also fires for
    added_by=="manual_click", not just "manual_exact". manual_click is
    TextAnchor's own established provenance for an operator-placed exact
    bbox (anchors/finder.py's apply_template_anchors already special-cases
    it when reapplying a matched template, and stamps the resulting
    SignMatch with added_by="manual_click") — but overlay.py's bypass only
    recognized "manual_exact" (the /v1/me/sign live-manual-placement
    convention introduced in v1.20.8), so a remembered manual placement,
    once reapplied via a green template match, fell through to the
    text-search placement path meant for auto-detected anchors, which has
    nothing nearby to search for at a freeform point.

v1.20.10 (Fix-8, verified against a real LibreOffice-converted document + real
LLM final_patterns — see TASK_fix8.md):
  - pdf/overlay.py: descender 0.45*sig_h (cap 22pt) -> 0.18*sig_h (cap 8pt).
    v1.20.9 overcorrected past the line entirely.
  - pipeline/auto1.py: _normalize_sameline no longer blindly rewrites [\\s\\S]
    -> [^\\n] on every LLM pattern. Reverse patterns (line->role, e.g.
    '_{3,}...Заказчик') are kept with their original cross-line reach —
    needed to catch footer signature blocks, where PyMuPDF extracts the
    underscore run and the role word as separate lines even though they're
    one text run in the DOCX. Forward patterns (role->line) get a smart
    same-line-with-one-optional-\\n rewrite instead of full multiline, so
    they can't jump into the next paragraph.
  - anchors/finder.py: removed the `is_reverse and has_multiline: continue`
    guard in find_signatures — dead code after the above (normalization no
    longer produces that combination for forward patterns, and reverse
    patterns need to survive specifically to catch footers).

v1.20.9:
  - pdf/overlay.py: bump signature descender 15%/6pt -> 45%/22pt so ink center
    lands ON the underscore line instead of ~15pt above it (Fix-7 followup)

v1.20.8:
  - pdf/overlay.py: SignMatch.added_by == "manual_exact" — freeform-размещение
    подписи (drag/resize в кабинете) обходит текстовый поиск линии, PNG
    вставляется буквально в переданный bbox (Fix-7, Phase B)

v1.20.7:
  - pdf/overlay.py: подпись заходит за линию вниз (~15% высоты, до 6pt) — визуально
    садится на линию, а не висит над ней
  - pdf/overlay.py: _find_line_below() — DocuSign-теги (\\tN\\) ищут реальную линию
    подписи ниже тега в той же колонке вместо фиксированного отступа

v1.20.6:
  - signature/processor.py: пропорциональный pad (2% bbox, [2..6]px) вместо фиксированного pad=12
  - intake/imap_source.py: poll() retry SELECT при IMAP4.error; _ensure_folder() сброс при исключении

v1.20.5:
  - ReviewResult.format_numbered(lang): нумерованный вывод (Замечания/Рекомендации)

v1.20.0:
  - signfinder.review: pre-flight ревью договора через LLM (опционально)
  - AnalysisResult.review, SignFinder.analyze(with_review=...), SignFinder.review()
  - Динамический промпт под язык/юрисдикцию (ru/pl/en/mk), два светофора

v1.16.0:
  - signfinder.intake: Protocol IntakeSource/IntakeSink, ImapSource, SmtpSink
  - build_processed_email helper для IMAP APPEND

v1.15.0:
  - Автотесты: unit (fingerprint, matcher, dedup, overlay, storage, sig_processor)
  - Integration-тесты с мок-LLM
  - dedup_anchors перенесён из Streamlit в core (signfinder.pipeline.dedup)
  - /v1/corpus endpoint (GET/PUT corpus.json)

v1.14.0:
  - apply_signature(): use_signature / use_marker / marker_color
  - Маркер места подписи (4×12мм, правое поле, pink/gray)
  - SignFinder.sign() пробрасывает новые параметры

v1.10.0:
  - Мульти-LLM: Anthropic + OpenAI + DeepSeek + Gemini через LLMClient abstraction
  - LLM конфиг через llm_config.json (UI) с fallback на env vars
  - SignFinder() без явного llm= — берёт провайдер из конфига
  - Backward compat: AnthropicClient(api_key=...) работает как раньше

v1.9.2:
  - AnalysisResult содержит поле fingerprint (dict).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from signfinder.anchors import (
    SignMatch,
    TextAnchor,
    apply_template_anchors,
    build_anchor_from_click,
    build_anchor_from_regex_match,
    parse_parties_json,
    regex_match_to_anchor,
)
from signfinder.config import Config
from signfinder.fingerprint import compute_fingerprint
from signfinder.llm import AnthropicClient, LLMClient, LLMError, create_client
from signfinder.pdf import (
    ParsedDocument,
    apply_signature,
    detect_language,
    detect_language_fast,
    parse_document,
    parse_pdf_bytes,
    render_page_with_highlights,
)
from signfinder.pipeline import (
    PipelineResult,
    apply_template_to_doc,
    detect_signer_profile,
    list_signer_profiles,
    load_signer_profile_by_id,
    run_pipeline_auto_1,
    save_pipeline_template,
    validate_with_llm,
)
from signfinder.pipeline.dedup import dedup_anchors
from signfinder.review import ReviewFinding, ReviewResult, review_contract
from signfinder.storage import StorageBackend, create_storage
from signfinder.templates import (
    DocumentTemplate,
    MatcherResult,
    add_anchors_to_template,
    find_matching_templates,
    list_templates,
    load_template,
    new_template,
    save_template,
    update_usage_stats,
)
from signfinder.traffic_light import classify

__version__ = "1.20.17"


# ── AnalysisResult ────────────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    """Итог SignFinder.analyze()."""
    traffic_light: str
    matcher_result: Optional[MatcherResult] = None
    applied_template: Optional[DocumentTemplate] = None
    anchors: list = field(default_factory=list)
    matches: list = field(default_factory=list)
    our_side: Optional[dict] = None
    error: Optional[str] = None
    pipeline_debug: dict = field(default_factory=dict)
    fingerprint: Optional[dict[str, Any]] = None
    detected_signer_id: Optional[str] = None
    review: Optional[dict] = None   # результат pre-flight ревью (v1.20), None если не запрашивали


# ── SignFinder facade ─────────────────────────────────────────────────────────

class SignFinder:

    def __init__(
        self,
        config: Optional[Config] = None,
        storage: Optional[StorageBackend] = None,
        llm: Optional[LLMClient] = None,
        **kwargs,
    ):
        self.config = config or Config.from_env(**kwargs)
        self.storage: StorageBackend = storage or create_storage(
            mode=self.config.storage_mode,
            path=self.config.storage_path,
            bucket=self.config.gcs_bucket,
        )

        if llm is not None:
            self.llm: LLMClient = llm
        else:
            try:
                self.llm = create_client()
            except RuntimeError:
                self.llm = AnthropicClient(
                    api_key=self.config.anthropic_api_key,
                    model=self.config.anthropic_model,
                )

    def _maybe_review(self, doc, language: str, with_review: bool) -> Optional[dict]:
        """Выполнить pre-flight ревью если запрошено. Не падает при ошибке."""
        if not with_review:
            return None
        try:
            full_text = "\n".join(p.text for p in doc.pages)
            page_count = len(doc.pages)
            rev = review_contract(full_text, language, self.llm, page_count=page_count)
            return rev.to_dict()
        except Exception as e:
            import sys
            sys.stderr.write(f"[analyze] review failed: {e}\n")
            return {"traffic_light": "yellow", "error": str(e), "findings": []}

    def review(self, contract_text: str, language: str, page_count: int = 0) -> "ReviewResult":
        """Pre-flight ревью договора напрямую. Независимо от поиска подписи."""
        return review_contract(contract_text, language, self.llm, page_count=page_count)

    def analyze(
        self,
        pdf_bytes: bytes,
        language: Optional[str] = None,
        filename: str = "document.pdf",
        with_review: bool = False,     # ← НОВОЕ: pre-flight ревью опционально
    ) -> "AnalysisResult":
        import fitz
        import time

        if not pdf_bytes or len(pdf_bytes) < 4:
            return AnalysisResult(
                traffic_light="no_match",
                error="pdf_bytes пустой или слишком маленький — невалидный PDF",
            )

        t0 = time.perf_counter()
        timings: dict[str, Any] = {}

        t_parse = time.perf_counter()
        doc = parse_pdf_bytes(pdf_bytes, filename=filename)
        timings["parse_ms"] = int((time.perf_counter() - t_parse) * 1000)
        timings["langdetect_calls"] = getattr(doc, "_langdetect_calls", 0)

        # БЫСТРАЯ детекция — для matcher достаточно языковой метки (fingerprint
        # матчит по simhash/jaccard, не по строке языка). LLM-fallback откладываем
        # до момента, когда станет ясно что идём в полный pipeline.
        t_lang_fast = time.perf_counter()
        lang_fast = language or detect_language_fast(doc)
        if not lang_fast or lang_fast == "unknown":
            lang_fast = "ru"
        timings["detect_lang_fast_ms"] = int((time.perf_counter() - t_lang_fast) * 1000)

        try:
            fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
            return AnalysisResult(
                traffic_light="no_match",
                error=f"Не удалось открыть PDF в fitz: {e}",
                pipeline_debug={"timings_ms": timings},
            )

        fp = None
        try:
            t_matcher = time.perf_counter()
            fp = compute_fingerprint(fitz_doc, lang_fast)
            matcher = find_matching_templates(
                fitz_doc, lang_fast,
                storage=self.storage,
                fingerprint=fp,
            )
            timings["matcher_ms"] = int((time.perf_counter() - t_matcher) * 1000)
        except Exception as e:
            timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
            return AnalysisResult(
                traffic_light="no_match",
                error=f"Matcher error: {e}",
                fingerprint=fp,
                pipeline_debug={"timings_ms": timings},
            )
        finally:
            fitz_doc.close()

        # Автоопределение профиля подписанта (Модель Б): по тексту первой+последней стр.
        t_profile = time.perf_counter()
        doc_text_for_detect = ""
        pages = doc.pages
        if pages:
            doc_text_for_detect = (pages[0].text or "")
            if len(pages) > 1:
                doc_text_for_detect += "\n" + (pages[-1].text or "")
        detected_signer_id = detect_signer_profile(self.storage, doc_text_for_detect)
        timings["detect_signer_profile_ms"] = int((time.perf_counter() - t_profile) * 1000)

        # ШАБЛОННЫЙ ПУТЬ: выходим БЕЗ LLM-вызова detect_language
        if matcher.traffic_light == "green" and matcher.best_match:
            tpl = load_template(self.storage, matcher.best_match.template_id)
            if tpl is not None:
                tpl_matches, tpl_anchors = apply_template_to_doc(doc, tpl, lang_fast)
                if tpl_anchors:
                    try:
                        update_usage_stats(self.storage, matcher.best_match.template_id, "applied")
                    except Exception:
                        pass
                    timings["detect_lang_llm_used"] = False
                    timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
                    timings["path"] = "template"
                    review_dict = self._maybe_review(doc, lang_fast, with_review)
                    return AnalysisResult(
                        traffic_light="green",
                        matcher_result=matcher,
                        applied_template=tpl,
                        matches=tpl_matches,
                        anchors=tpl_anchors,
                        fingerprint=fp,
                        detected_signer_id=detected_signer_id,
                        pipeline_debug={"timings_ms": timings},
                        review=review_dict,
                    )

        # ПОЛНЫЙ ПАЙПЛАЙН — точная детекция с LLM-fallback при необходимости.
        t_lang = time.perf_counter()
        lang = language or detect_language(doc, llm=self.llm)
        if not lang or lang == "unknown":
            lang = lang_fast  # fallback на быстрый результат, а не на "ru"
        timings["detect_lang_ms"] = int((time.perf_counter() - t_lang) * 1000)
        timings["detect_lang_llm_used"] = timings["detect_lang_ms"] > 200

        t_pipeline = time.perf_counter()
        pipeline = run_pipeline_auto_1(
            doc=doc,
            language=lang,
            storage=self.storage,
            llm=self.llm,
            signer_id=detected_signer_id,
        )
        timings["pipeline_ms"] = int((time.perf_counter() - t_pipeline) * 1000)
        timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
        timings["path"] = "pipeline"

        debug = dict(pipeline.debug) if pipeline.debug else {}
        debug["timings_ms"] = timings

        if not pipeline.ok:
            return AnalysisResult(
                traffic_light=matcher.traffic_light,
                matcher_result=matcher,
                error=pipeline.error,
                pipeline_debug=debug,
                fingerprint=fp,
                detected_signer_id=detected_signer_id,
            )

        review_dict = self._maybe_review(doc, lang, with_review)
        return AnalysisResult(
            traffic_light=matcher.traffic_light,
            matcher_result=matcher,
            anchors=pipeline.anchors,
            matches=pipeline.matches,
            our_side=pipeline.our_side,
            pipeline_debug=debug,
            fingerprint=fp,
            detected_signer_id=detected_signer_id,
            review=review_dict,
        )

    def sign(
        self,
        pdf_bytes: bytes,
        anchors_or_matches: list,
        png_bytes: bytes | None,
        flatten: bool = False,
        scale: float = 1.0,
        use_signature: bool = True,
        use_marker: bool = False,
        marker_color: str = "pink",
    ) -> bytes:
        sign_mode = self.storage.read_json("settings/sign_mode.json") or {}
        sign_above_line = sign_mode.get("sign_above_line", False)
        matches = [self._to_match(a) for a in anchors_or_matches]
        return apply_signature(
            pdf_bytes, matches, png_bytes,
            flatten=flatten, scale=scale,
            use_signature=use_signature,
            use_marker=use_marker,
            marker_color=marker_color,
            sign_above_line=sign_above_line,
        )

    def build_anchor_from_click(self, pdf_bytes: bytes, page: int, x: float, y: float, language: str = "ru") -> Optional[TextAnchor]:
        import fitz
        if not pdf_bytes or len(pdf_bytes) < 4:
            return None
        try:
            fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception:
            return None
        try:
            return build_anchor_from_click(fitz_doc, page, x, y, language)
        finally:
            fitz_doc.close()

    @staticmethod
    def _to_match(item) -> SignMatch:
        if isinstance(item, SignMatch):
            return item
        if isinstance(item, TextAnchor):
            ph = item.page_hint
            if ph == "first":
                page = 0
            elif ph == "last":
                page = -1
            else:
                try:
                    page = int(ph)
                except (ValueError, TypeError):
                    page = 0
            return SignMatch(id=item.id, page=page, bbox=item.bbox, context=item.anchor_text,
                             party="anchor", pattern=item.generated_pattern, confidence=1.0,
                             added_by=item.added_by)
        raise TypeError(f"Expected SignMatch or TextAnchor, got {type(item).__name__}")


__all__ = [
    "__version__", "SignFinder", "AnalysisResult", "Config", "StorageBackend",
    "create_storage", "LLMClient", "LLMError", "AnthropicClient", "create_client",
    "ParsedDocument", "parse_document", "parse_pdf_bytes", "apply_signature",
    "render_page_with_highlights", "detect_language", "TextAnchor", "SignMatch",
    "build_anchor_from_click", "build_anchor_from_regex_match", "regex_match_to_anchor",
    "apply_template_anchors", "parse_parties_json", "DocumentTemplate", "MatcherResult",
    "find_matching_templates", "list_templates", "load_template", "save_template",
    "new_template", "update_usage_stats", "add_anchors_to_template", "compute_fingerprint",
    "classify", "run_pipeline_auto_1", "PipelineResult", "apply_template_to_doc",
    "save_pipeline_template", "validate_with_llm", "dedup_anchors",
    "detect_signer_profile", "list_signer_profiles", "load_signer_profile_by_id",
    "ReviewResult", "ReviewFinding", "review_contract",
]
