"""TextAnchor-система: модели, builder из кликов/regex, finder."""
from signfinder.anchors.builder import (
    build_anchor_from_click,
    build_anchor_from_regex_match,
    has_anchor_at,
)
from signfinder.anchors.finder import (
    apply_template_anchors,
    find_signatures,
    find_signatures_smart,
    parse_parties_json,
    parse_parties_md,
    regex_match_to_anchor,
)
from signfinder.anchors.models import SignMatch, TextAnchor

__all__ = [
    "TextAnchor",
    "SignMatch",
    "build_anchor_from_click",
    "build_anchor_from_regex_match",
    "has_anchor_at",
    "find_signatures",
    "find_signatures_smart",
    "apply_template_anchors",
    "regex_match_to_anchor",
    "parse_parties_json",
    "parse_parties_md",
]
