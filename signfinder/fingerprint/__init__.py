"""Fingerprint документа: simhash + структура для matching шаблонов."""
from signfinder.fingerprint.computer import (
    compute_fingerprint,
    compute_header_simhash,
    extract_section_titles,
    find_header_page,
)

__all__ = [
    "compute_fingerprint",
    "find_header_page",
    "compute_header_simhash",
    "extract_section_titles",
]
