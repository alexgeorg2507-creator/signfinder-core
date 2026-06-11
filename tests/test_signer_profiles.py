"""Тесты профилей подписантов (v1.18.3-v1.18.5)."""
from __future__ import annotations

import pytest

from signfinder.pipeline.settings import (
    detect_signer_profile,
    get_aliases_for_language,
    list_signer_profiles,
    load_signer_profile_by_id,
)


# ── Фикстура с двумя профилями ───────────────────────────────────────────────

@pytest.fixture
def storage_with_profiles(local_storage):
    """Storage с двумя профилями: default (Лебедев) и borisov (Innowise)."""
    local_storage.write_json("signers/default/profile.json", {
        "id": "default",
        "display": "Лебедев / Инлайн технолоджис",
        "match_markers": ["Инлайн технолоджис", "Лебедев"],
        "company_aliases": [
            {"language": "ru", "value": "ООО Инлайн технолоджис"},
        ],
        "signer_aliases": [
            {"language": "ru", "value": "Лебедев А.П."},
        ],
    })
    local_storage.write_json("signers/borisov/profile.json", {
        "id": "borisov",
        "display": "Vadim Borisov / Innowise",
        "match_markers": ["Innowise", "Vadim Borisov", "Вадим Борисов"],
        "company_aliases": [
            {"language": "en", "value": "Innowise Sp. z o.o"},
            {"language": "pl", "value": "Innowise Sp. z o.o"},
            {"language": "mk", "value": "Innowise"},
        ],
        "signer_aliases": [
            {"language": "en", "value": "Vadim Borisov"},
            {"language": "mk", "value": "Вадим Борисов"},
        ],
    })
    return local_storage


# ── list_signer_profiles ─────────────────────────────────────────────────────

def test_list_signer_profiles_returns_all(storage_with_profiles):
    profiles = list_signer_profiles(storage_with_profiles)
    ids = {p["id"] for p in profiles}
    assert ids == {"default", "borisov"}


def test_list_signer_profiles_empty_storage(local_storage):
    assert list_signer_profiles(local_storage) == []


def test_list_signer_profiles_none_storage():
    assert list_signer_profiles(None) == []


# ── load_signer_profile_by_id ────────────────────────────────────────────────

def test_load_profile_by_id_existing(storage_with_profiles):
    p = load_signer_profile_by_id(storage_with_profiles, "borisov")
    assert p["id"] == "borisov"
    assert "Innowise" in p["match_markers"]


def test_load_profile_missing_returns_empty(storage_with_profiles):
    p = load_signer_profile_by_id(storage_with_profiles, "nonexistent")
    assert p["id"] == "nonexistent"
    assert p["company_aliases"] == []


def test_load_default_legacy_fallback(local_storage):
    """default читается из корневого signer_profile.json если нет signers/default/."""
    local_storage.write_json("signer_profile.json", {
        "company_aliases": [{"language": "ru", "value": "Legacy Co"}],
        "signer_aliases": [{"language": "ru", "value": "Legacy Signer"}],
    })
    p = load_signer_profile_by_id(local_storage, "default")
    assert p["id"] == "default"
    assert any("Legacy Co" in a["value"] for a in p["company_aliases"])


# ── detect_signer_profile (Модель Б) ─────────────────────────────────────────

def test_detect_innowise_document(storage_with_profiles):
    text = "This agreement is between ... and Innowise Sp. z o.o (d/b/a Innowise Group) ..."
    assert detect_signer_profile(storage_with_profiles, text) == "borisov"


def test_detect_russian_document(storage_with_profiles):
    text = "Договор между ООО Инлайн технолоджис в лице Лебедева А.П. и ..."
    assert detect_signer_profile(storage_with_profiles, text) == "default"


def test_detect_unknown_falls_back_to_default(storage_with_profiles):
    """Документ без известных маркеров → default."""
    text = "Some unrelated document about cats and dogs."
    assert detect_signer_profile(storage_with_profiles, text) == "default"


def test_detect_no_profiles_returns_default(local_storage):
    """Нет профилей → возврат default_id."""
    assert detect_signer_profile(local_storage, "any text") == "default"


def test_detect_macedonian_cyrillic_borisov(storage_with_profiles):
    """Македонский текст с 'Вадим Борисов' → borisov."""
    text = "Договорот е помеѓу ... и Innowise со потпис Вадим Борисов."
    assert detect_signer_profile(storage_with_profiles, text) == "borisov"


# ── get_aliases_for_language: составной язык ─────────────────────────────────

def test_aliases_single_language(storage_with_profiles):
    """Одиночный язык: только en алиасы для borisov."""
    aliases = get_aliases_for_language(storage_with_profiles, "en", signer_id="borisov")
    assert "Innowise Sp. z o.o" in aliases["company"]
    assert "Vadim Borisov" in aliases["signer"]
    # Македонские не попадают
    assert "Вадим Борисов" not in aliases["signer"]


def test_aliases_composite_language_mk_en(storage_with_profiles):
    """Составной язык 'mk, en' → алиасы ОБОИХ языков (для dual-column)."""
    aliases = get_aliases_for_language(storage_with_profiles, "mk, en", signer_id="borisov")
    assert "Vadim Borisov" in aliases["signer"]
    assert "Вадим Борисов" in aliases["signer"]
    assert "Innowise Sp. z o.o" in aliases["company"]


def test_aliases_fallback_to_all_when_lang_unknown(storage_with_profiles):
    """Запрошен 'fr' (нет такого языка) → fallback на все алиасы."""
    aliases = get_aliases_for_language(storage_with_profiles, "fr", signer_id="borisov")
    # Все алиасы должны прийти fallback'ом
    assert len(aliases["signer"]) > 0
    assert len(aliases["company"]) > 0


def test_aliases_default_signer_id(storage_with_profiles):
    """Без указания signer_id → default."""
    aliases = get_aliases_for_language(storage_with_profiles, "ru")
    assert "ООО Инлайн технолоджис" in aliases["company"]
