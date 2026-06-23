"""SignFinder — core engine for automatic signature placement in contracts.

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

__version__ = "1.20.0"


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
                             party="anchor", pattern=item.generated_pattern, confidence=1.0)
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
