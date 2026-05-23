"""Pipeline-оркестратор и сопутствующие функции.

- auto1.run_pipeline_auto_1  — step3+step4+step5 (точный перенос v1.8)
- auto1.apply_template_to_doc — применение шаблона без session_state
- auto1.save_pipeline_template — сохранение DocumentTemplate
- party_resolver.resolve_party — определение стороны по ФИО (отдельный шаг, не в пайплайне)
- pattern_extractor           — генерация/сужение regex (расширенная версия для API)
- llm_finder                  — LLM-only fallback поиска (не входит в базовый пайплайн)
- validator.validate_with_llm — LLM-валидация (вызывается явно после run_pipeline_auto_1)
- settings                    — markers/signer_profile через StorageBackend
"""
from signfinder.pipeline.auto1 import (
    PipelineResult,
    apply_template_to_doc,
    run_pipeline_auto_1,
    run_step3,
    run_step4,
    run_step5,
    save_pipeline_template,
)
from signfinder.pipeline.llm_finder import find_signatures_llm
from signfinder.pipeline.party_resolver import resolve_party
from signfinder.pipeline.pattern_extractor import (
    count_matches,
    extract_patterns,
    merge_patterns_into_json,
    narrow_patterns,
    patterns_match_anything,
    per_pattern_counts,
    regenerate_from_raw_text,
)
from signfinder.pipeline.settings import (
    get_aliases_for_language,
    get_markers_for_language,
    load_markers,
    load_signer_profile,
    save_markers,
    save_signer_profile,
)
from signfinder.pipeline.validator import validate_with_llm

__all__ = [
    # Auto1 pipeline
    "run_pipeline_auto_1",
    "PipelineResult",
    "run_step3",
    "run_step4",
    "run_step5",
    "apply_template_to_doc",
    "save_pipeline_template",
    # Validator (вызывается явно, не входит в базовый пайплайн)
    "validate_with_llm",
    # Party resolver (отдельная операция)
    "resolve_party",
    # Pattern extractor (расширенные операции для API)
    "extract_patterns",
    "merge_patterns_into_json",
    "regenerate_from_raw_text",
    "narrow_patterns",
    "patterns_match_anything",
    "count_matches",
    "per_pattern_counts",
    # LLM finder (fallback, не в базовом пайплайне)
    "find_signatures_llm",
    # Settings
    "load_markers",
    "save_markers",
    "get_markers_for_language",
    "load_signer_profile",
    "save_signer_profile",
    "get_aliases_for_language",
]
