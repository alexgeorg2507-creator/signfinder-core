"""Реестр шаблонов: модели, CRUD, matcher."""
from signfinder.templates.matcher import (
    build_explanation,
    compute_composite_score,
    find_matching_templates,
    log_matching_decision,
    passes_quick_filter,
)
from signfinder.templates.models import DocumentTemplate, MatcherResult, MatchResult
from signfinder.templates.storage import (
    add_anchors_to_template,
    delete_template,
    generate_template_name,
    list_templates,
    load_template,
    new_template,
    save_template,
    update_usage_stats,
)

__all__ = [
    # Models
    "DocumentTemplate",
    "MatchResult",
    "MatcherResult",
    # Storage CRUD
    "save_template",
    "load_template",
    "list_templates",
    "delete_template",
    "new_template",
    "generate_template_name",
    "update_usage_stats",
    "add_anchors_to_template",
    # Matcher
    "find_matching_templates",
    "compute_composite_score",
    "passes_quick_filter",
    "build_explanation",
    "log_matching_decision",
]
