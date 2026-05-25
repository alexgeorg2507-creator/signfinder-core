"""SignFinder — core engine for automatic signature placement in contracts.

Главный фасад — класс SignFinder. Покрывает 90% сценариев.

Тонкие сценарии — прямые импорты подмодулей:
    from signfinder.anchors import find_signatures, build_anchor_from_click
    from signfinder.templates import find_matching_templates
    from signfinder.pdf import apply_signature
    from signfinder.pipeline import run_pipeline_auto_1, validate_with_llm

FIX v1.9.1:
  - analyze(): guard на пустой pdf_bytes перед fitz.open
  - analyze(): отдельный try/except на fitz.open с человекочитаемым сообщением
    вместо голого PyMuPDF Exception("0")
  - build_anchor_from_click(): аналогичный guard
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

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
from signfinder.llm import AnthropicClient, LLMClient, LLMError
from signfinder.pdf import (
    ParsedDocument,
    apply_signature,
    detect_language,
    parse_document,
    parse_pdf_bytes,
    render_page_with_highlights,
)
from signfinder.pipeline import (
    PipelineResult,
    apply_template_to_doc,
    run_pipeline_auto_1,
    save_pipeline_template,
    validate_with_llm,
)
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

__version__ = "1.9.0"


# ── AnalysisResult ────────────────────────────────────────────────────────────

@dataclass
class AnalysisResult:
    """Итог SignFinder.analyze()."""
    traffic_light: str                               # "green" | "yellow" | "no_match"
    matcher_result: Optional[MatcherResult] = None
    applied_template: Optional[DocumentTemplate] = None
    anchors: list = field(default_factory=list)      # list[TextAnchor]
    matches: list = field(default_factory=list)      # list[SignMatch]
    our_side: Optional[dict] = None
    error: Optional[str] = None
    pipeline_debug: dict = field(default_factory=dict)


# ── SignFinder facade ─────────────────────────────────────────────────────────

class SignFinder:
    """Facade для SignFinder core."""

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
        self.llm: LLMClient = llm or AnthropicClient(
            api_key=self.config.anthropic_api_key,
            model=self.config.anthropic_model,
        )

    def analyze(
        self,
        pdf_bytes: bytes,
        language: Optional[str] = None,
        filename: str = "document.pdf",
    ) -> AnalysisResult:
        """Полный анализ документа: matcher → шаблон (зелёный) | pipeline (жёлтый)."""
        import fitz

        # Guard: пустой или слишком маленький файл
        if not pdf_bytes or len(pdf_bytes) < 4:
            return AnalysisResult(
                traffic_light="no_match",
                error="pdf_bytes пустой или слишком маленький — невалидный PDF",
            )

        # 0. Parse
        doc = parse_pdf_bytes(pdf_bytes, filename=filename)

        # 0. Language
        lang = language or detect_language(doc, llm=self.llm)
        if not lang or lang == "unknown":
            lang = "ru"

        # 0. Fingerprint + Matcher
        # FIX: отдельный try на fitz.open — PyMuPDF бросает Exception("0")
        # для повреждённых/зашифрованных PDF вместо нормального исключения
        try:
            fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        except Exception as e:
            return AnalysisResult(
                traffic_light="no_match",
                error=f"Не удалось открыть PDF в fitz (повреждён или зашифрован?): {e}",
            )

        try:
            fp = compute_fingerprint(fitz_doc, lang)
            matcher = find_matching_templates(
                fitz_doc, lang,
                storage=self.storage,
                fingerprint=fp,
            )
        except Exception as e:
            return AnalysisResult(
                traffic_light="no_match",
                error=f"Matcher error: {e}",
            )
        finally:
            fitz_doc.close()

        # Зелёный → применить шаблон
        if matcher.traffic_light == "green" and matcher.best_match:
            tpl = load_template(self.storage, matcher.best_match.template_id)
            if tpl is not None:
                tpl_matches, tpl_anchors = apply_template_to_doc(doc, tpl, lang)
                if tpl_anchors:
                    try:
                        update_usage_stats(self.storage, matcher.best_match.template_id, "applied")
                    except Exception:
                        pass
                    return AnalysisResult(
                        traffic_light="green",
                        matcher_result=matcher,
                        applied_template=tpl,
                        matches=tpl_matches,
                        anchors=tpl_anchors,
                    )
                # Якоря не применились — падаем на pipeline
            # Шаблон не нашёлся — падаем на pipeline

        # Жёлтый или шаблон не применился → run_pipeline_auto_1
        pipeline = run_pipeline_auto_1(
            doc=doc,
            language=lang,
            storage=self.storage,
            llm=self.llm,
        )

        if not pipeline.ok:
            return AnalysisResult(
                traffic_light=matcher.traffic_light,
                matcher_result=matcher,
                error=pipeline.error,
                pipeline_debug=pipeline.debug,
            )

        return AnalysisResult(
            traffic_light=matcher.traffic_light,
            matcher_result=matcher,
            anchors=pipeline.anchors,
            matches=pipeline.matches,
            our_side=pipeline.our_side,
            pipeline_debug=pipeline.debug,
        )

    def sign(
        self,
        pdf_bytes: bytes,
        anchors_or_matches: list,
        png_bytes: bytes,
        flatten: bool = False,
    ) -> bytes:
        """Наложить PNG-подпись на PDF."""
        matches = [self._to_match(a) for a in anchors_or_matches]
        return apply_signature(pdf_bytes, matches, png_bytes, flatten=flatten)

    def build_anchor_from_click(
        self,
        pdf_bytes: bytes,
        page: int,
        x: float,
        y: float,
        language: str = "ru",
    ) -> Optional[TextAnchor]:
        """Строит TextAnchor по клику (ручная доразметка)."""
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

    # ── helpers ───────────────────────────────────────────────────────────

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
            return SignMatch(
                id=item.id,
                page=page,
                bbox=item.bbox,
                context=item.anchor_text,
                party="anchor",
                pattern=item.generated_pattern,
                confidence=1.0,
            )
        raise TypeError(f"Expected SignMatch or TextAnchor, got {type(item).__name__}")


__all__ = [
    "__version__",
    "SignFinder",
    "AnalysisResult",
    "Config",
    "StorageBackend",
    "create_storage",
    "LLMClient",
    "LLMError",
    "AnthropicClient",
    "ParsedDocument",
    "parse_document",
    "parse_pdf_bytes",
    "apply_signature",
    "render_page_with_highlights",
    "detect_language",
    "TextAnchor",
    "SignMatch",
    "build_anchor_from_click",
    "build_anchor_from_regex_match",
    "regex_match_to_anchor",
    "apply_template_anchors",
    "parse_parties_json",
    "DocumentTemplate",
    "MatcherResult",
    "find_matching_templates",
    "list_templates",
    "load_template",
    "save_template",
    "new_template",
    "update_usage_stats",
    "add_anchors_to_template",
    "compute_fingerprint",
    "classify",
    "run_pipeline_auto_1",
    "PipelineResult",
    "apply_template_to_doc",
    "save_pipeline_template",
    "validate_with_llm",
]
