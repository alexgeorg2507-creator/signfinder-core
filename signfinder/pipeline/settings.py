"""Конфигурационные блоки используемые в пайплайне:
- markers (универсальные маркеры подписи per language)
- signer_profile (алиасы компании и подписанта per language)

ПЕРЕНОС из core/markers.py и core/signer_profile.py с заменой на StorageBackend.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from typing import Optional

from signfinder.storage.base import StorageBackend

_MARKERS_FILE = "markers.json"
_SIGNER_PROFILE_FILE = "signer_profile.json"


# ── Markers ────────────────────────────────────────────────────────────────

MARKERS_DEFAULTS: dict = {
    "version": "1.0",
    "languages": {
        "ru": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Подпись", "М.П.", "Место подписи", "Подп.", "/Подпись/"],
            "section_anchors": ["раздел", "приложение", "акт", "часть"],
        },
        "en": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Signature", "Sign", "/Signature/", "Authorized Signatory"],
            "section_anchors": ["section", "annex", "appendix", "schedule"],
        },
        "pl": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Podpis", "Czytelny podpis", "Pieczęć"],
            "section_anchors": ["część", "załącznik", "rozdział"],
        },
    },
}


def load_markers(storage: Optional[StorageBackend]) -> dict:
    """Загрузить маркеры из storage. Fallback на defaults."""
    if storage is None:
        return dict(MARKERS_DEFAULTS)
    try:
        data = storage.read_json(_MARKERS_FILE)
        if data:
            return data
    except Exception as e:
        sys.stderr.write(f"[settings] load_markers error: {e}\n")
    return dict(MARKERS_DEFAULTS)


def save_markers(storage: StorageBackend, markers: dict) -> None:
    storage.write_json(_MARKERS_FILE, markers)


def get_markers_for_language(storage: Optional[StorageBackend], language: str) -> dict:
    """Вернуть блок маркеров для языка. Пустой dict если нет."""
    markers = load_markers(storage)
    lang = (language or "").lower()[:2]
    return markers.get("languages", {}).get(lang, {})


# ── Signer profile ────────────────────────────────────────────────────────

SIGNER_PROFILE_DEFAULTS: dict = {
    "version": "1.0",
    "company_aliases": [],   # [{"language": "ru", "value": "ООО Ромашка"}, ...]
    "signer_aliases": [],
    "updated_at": "",
}


def load_signer_profile(storage: Optional[StorageBackend]) -> dict:
    """Загрузить профиль подписанта. Fallback на пустой."""
    if storage is None:
        return dict(SIGNER_PROFILE_DEFAULTS)
    try:
        data = storage.read_json(_SIGNER_PROFILE_FILE)
        if data:
            result = dict(SIGNER_PROFILE_DEFAULTS)
            result.update(data)
            return result
    except Exception as e:
        sys.stderr.write(f"[settings] load_signer_profile error: {e}\n")
    return dict(SIGNER_PROFILE_DEFAULTS)


def save_signer_profile(storage: StorageBackend, profile: dict) -> None:
    profile = dict(profile)
    profile["updated_at"] = datetime.now(timezone.utc).isoformat()
    storage.write_json(_SIGNER_PROFILE_FILE, profile)


def get_aliases_for_language(
    storage: Optional[StorageBackend],
    language: str,
) -> dict[str, list[str]]:
    """Вернуть {company: [...], signer: [...]} алиасов для языка.

    Fallback: если для языка пусто — возвращает все.
    """
    profile = load_signer_profile(storage)
    lang = (language or "").lower()[:2]

    def _filter(key: str) -> list[str]:
        all_aliases = profile.get(key, [])
        by_lang = [a["value"] for a in all_aliases
                   if a.get("language") == lang and a.get("value", "").strip()]
        if by_lang:
            return by_lang
        return [a["value"] for a in all_aliases if a.get("value", "").strip()]

    return {
        "company": _filter("company_aliases"),
        "signer": _filter("signer_aliases"),
    }
