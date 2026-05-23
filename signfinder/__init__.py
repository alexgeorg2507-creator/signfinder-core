"""SignFinder — core engine for automatic signature placement in contracts.

Главный фасад — класс SignFinder. Покрывает 90% сценариев.

Тонкие сценарии — прямые импорты подмодулей:
    from signfinder.anchors import find_signatures, build_anchor_from_click
    from signfinder.templates import find_matching_templates
    from signfinder.pdf import apply_signature
    from signfinder.pipeline import run_pipeline_auto_1, validate_with_llm
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
    """Facade для SignFinder core.

    Минимальный пример:
        sf = SignFinder()                # читает env vars
        result = sf.analyze(pdf_bytes, language="ru")
        signed = sf.sign(pdf_bytes, result.anchors, png_bytes)

    Явная конфигурация:
        sf = SignFinder(storage_mode="local", storage_path="./data",
                        anthropic_api_key="sk-ant-...")
    """

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
        """Полный анализ документа: matcher → шаблон (зелёный) | pipeline (жёлтый).

        Точное соответствие флоу pages/5_🤖_Авто_подписание.py v1.8:
          0. parse + detect_language + compute_fingerprint
          0. find_matching_templates (шаг 0 матчер)
          🟢 → apply_template_to_doc → AnalysisResult
          🟡 → run_pipeline_auto_1 (step3+step4+step5) → AnalysisResult

        Валидатор НЕ вызывается автоматически — как в оригинале.
        Вызвать явно если нужно:
            validate_with_llm(result.matches, party_name, sf.llm, rules)
        """
        import fitz

        # 0. Parse
        doc = parse_pdf_bytes(pdf_bytes, filename=filename)

        # 0. Language
        lang = language or detect_language(doc, llm=self.llm)
        if not lang or lang == "unknown":
            lang = "ru"

        # 0. Fingerprint + Matcher
        try:
            fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            try:
                fp = compute_fingerprint(fitz_doc, lang)
                matcher = find_matching_templates(
                    fitz_doc, lang,
                    storage=self.storage,
                    fingerprint=fp,
                )
            finally:
                fitz_doc.close()
        except Exception as e:
            return AnalysisResult(
                traffic_light="no_match",
                error=f"Matcher error: {e}",
            )

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
        """Наложить PNG-подпись на PDF.

        Принимает list[TextAnchor] или list[SignMatch].
        TextAnchor конвертируется в SignMatch через _to_match().
        """
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
        fitz_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
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
    # Facade
    "SignFinder",
    "AnalysisResult",
    # Config
    "Config",
    # Storage
    "StorageBackend",
    "create_storage",
    # LLM
    "LLMClient",
    "LLMError",
    "AnthropicClient",
    # PDF
    "ParsedDocument",
    "parse_document",
    "parse_pdf_bytes",
    "apply_signature",
    "render_page_with_highlights",
    "detect_language",
    # Anchors
    "TextAnchor",
    "SignMatch",
    "build_anchor_from_click",
    "build_anchor_from_regex_match",
    "regex_match_to_anchor",
    "apply_template_anchors",
    "parse_parties_json",
    # Templates
    "DocumentTemplate",
    "MatcherResult",
    "find_matching_templates",
    "list_templates",
    "load_template",
    "save_template",
    "new_template",
    "update_usage_stats",
    "add_anchors_to_template",
    # Fingerprint
    "compute_fingerprint",
    # Traffic light
    "classify",
    # Pipeline
    "run_pipeline_auto_1",
    "PipelineResult",
    "apply_template_to_doc",
    "save_pipeline_template",
    "validate_with_llm",
]
