"""Конфигурационные блоки используемые в пайплайне:
- markers (универсальные маркеры подписи per language)
- signer_profile (алиасы компании и подписанта per language)

ПЕРЕНОС из core/markers.py и core/signer_profile.py с заменой на StorageBackend.
"""
from __future__ import annotations

import sys
import time as _time
from datetime import datetime, timezone
from typing import Any, Optional

from signfinder.storage.base import StorageBackend

# ── TTL-кэш для markers и signer profiles ─────────────────────────────────────
# Снижает число read_json с диска: за один analyze() load_markers зовётся 3-5×
# для разных языков, load_signer_profile_by_id — 2-3×. TTL=60с: изменения через
# UI применяются не позднее чем через минуту.

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL = 60.0  # секунды


def _cached(key: str, loader):
    now = _time.monotonic()
    entry = _CACHE.get(key)
    if entry is not None and now - entry[0] < _TTL:
        return entry[1]
    result = loader()
    _CACHE[key] = (now, result)
    return result


def _cache_invalidate(prefix: str) -> None:
    """Сбросить записи кэша с данным префиксом. Вызывается при PUT/PATCH."""
    for k in list(_CACHE.keys()):
        if k.startswith(prefix):
            del _CACHE[k]

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
            "signature_block_patterns": ["_{3,}\\s*\\([^)]{3,40}\\)"],
        },
        "en": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Signature", "Sign", "/Signature/", "Authorized Signatory"],
            "section_anchors": ["section", "annex", "appendix", "schedule"],
            "signature_block_patterns": ["_{3,}\\s*\\([^)]{3,40}\\)"],
        },
        "pl": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": ["Podpis", "Czytelny podpis", "Pieczęć"],
            "section_anchors": ["część", "załącznik", "rozdział"],
            "signature_block_patterns": ["_{3,}\\s*\\([^)]{3,40}\\)"],
        },
        "mk": {
            "underline_patterns": ["_{3,}", "\\.{5,}"],
            "marker_words": [
                "Потпис", "УПРАВИТЕЛ", "ОВЛАСТЕНО ЛИЦЕ", "Директор",
                "Потпишани", "М.П.", "Место за потпис",
            ],
            "section_anchors": ["дел", "прилог", "анекс", "додаток"],
            "signature_block_patterns": ["_{3,}\\s*\\([^)]{3,40}\\)"],
        },
    },
}


def load_markers(storage: Optional[StorageBackend]) -> dict:
    """Загрузить маркеры из storage. Fallback на defaults. Результат кэшируется TTL=60с."""
    if storage is None:
        return dict(MARKERS_DEFAULTS)
    key = f"markers:{id(storage)}"

    def _load():
        try:
            data = storage.read_json(_MARKERS_FILE)
            if data:
                return data
        except Exception as e:
            sys.stderr.write(f"[settings] load_markers error: {e}\n")
        return dict(MARKERS_DEFAULTS)

    return _cached(key, _load)


def save_markers(storage: StorageBackend, markers: dict) -> None:
    storage.write_json(_MARKERS_FILE, markers)


def get_markers_for_language(storage: Optional[StorageBackend], language: str) -> dict:
    """Вернуть блок маркеров для языка. Пустой dict если нет."""
    markers = load_markers(storage)
    lang = (language or "").lower()[:2]
    return markers.get("languages", {}).get(lang, {})


def get_markers_for_languages(
    storage: Optional[StorageBackend],
    languages: list,
) -> dict:
    """Вернуть объединённый блок маркеров для списка языков.

    Используется для двуязычных документов (dual_column_vertical).
    Списки объединяются без дублей. Скалярные значения — из первого языка.

    Пример: ["en", "mk"] → marker_words включает и "Signature" и "Потпис".
    """
    if not languages:
        return {}
    result: dict = {}
    for lang in languages:
        block = get_markers_for_language(storage, lang)
        for key, val in block.items():
            if isinstance(val, list):
                existing = result.setdefault(key, [])
                for item in val:
                    if item not in existing:
                        existing.append(item)
            else:
                result.setdefault(key, val)
    return result


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
    signer_id: str = "default",
) -> dict[str, list[str]]:
    """Вернуть {company: [...], signer: [...]} алиасов для языка КОНКРЕТНОГО профиля.

    Поддерживает составной язык ("mk, en") — возвращает алиасы для ВСЕХ языков.
    Fallback: если для запрошенных языков пусто — возвращает все алиасы.
    """
    profile = load_signer_profile_by_id(storage, signer_id)
    # Составной язык ("mk, en") → набор кодов {"mk", "en"}
    raw_langs = (language or "")
    langs = {l.strip()[:2].lower() for l in raw_langs.split(",") if l.strip()}
    if not langs:
        langs = {"ru"}

    def _filter(key: str) -> list[str]:
        all_aliases = profile.get(key, [])
        # Берём алиасы для ВСЕХ запрошенных языков
        by_langs = [
            a["value"] for a in all_aliases
            if a.get("language", "")[:2].lower() in langs
            and a.get("value", "").strip()
        ]
        if by_langs:
            return by_langs
        # Fallback: все алиасы (как раньше)
        return [a["value"] for a in all_aliases if a.get("value", "").strip()]

    return {
        "company": _filter("company_aliases"),
        "signer": _filter("signer_aliases"),
    }


# ── Multi-signer profiles ──────────────────────────────────────────────────────

_SIGNERS_PREFIX = "signers/"


def list_signer_profiles(storage: Optional[StorageBackend]) -> list[dict]:
    """Список всех профилей подписантов из signers/*/profile.json."""
    if storage is None:
        return []
    out = []
    try:
        keys = storage.list_prefix(_SIGNERS_PREFIX)
    except Exception:
        keys = []
    seen: set = set()
    for k in keys:
        parts = k[len(_SIGNERS_PREFIX):].split("/")
        if len(parts) >= 2 and parts[1] == "profile.json":
            sid = parts[0]
            if sid in seen:
                continue
            seen.add(sid)
            data = storage.read_json(k)
            if data:
                data.setdefault("id", sid)
                out.append(data)
    return out


def load_signer_profile_by_id(storage: Optional[StorageBackend], signer_id: str) -> dict:
    """Профиль по id. Fallback на legacy signer_profile.json (корень) для 'default'.
    Результат кэшируется TTL=60с."""
    key = f"profile:{id(storage)}:{signer_id}"

    def _load():
        if storage is not None:
            data = storage.read_json(f"{_SIGNERS_PREFIX}{signer_id}/profile.json")
            if data:
                data.setdefault("id", signer_id)
                return data
        if signer_id == "default":
            legacy = load_signer_profile(storage)
            legacy.setdefault("id", "default")
            return legacy
        return {"id": signer_id, "company_aliases": [], "signer_aliases": [], "match_markers": []}

    return _cached(key, _load)


def detect_signer_profile(
    storage: Optional[StorageBackend],
    doc_text: str,
    default_id: str = "default",
) -> str:
    """Автоопределение профиля по содержимому документа (Модель Б).

    Считает совпадения match_markers каждого профиля в тексте.
    Возвращает signer_id с максимальным числом совпадений. Fallback на default_id.
    """
    profiles = list_signer_profiles(storage)
    if not profiles:
        return default_id
    text_low = (doc_text or "").lower()
    best_id, best_score = default_id, 0
    for p in profiles:
        score = 0
        for marker in p.get("match_markers", []):
            m = (marker or "").strip().lower()
            if m and m in text_low:
                score += 1
        if score > best_score:
            best_score, best_id = score, p.get("id", default_id)
    return best_id
