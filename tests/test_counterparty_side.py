"""Тесты _build_counterparty_side (Deal Cycle, 2026-07-25).

Deal Cycle требует якоря обеих сторон из одного анализа (см.
TASK_deal_cycle_E2.md) — _build_counterparty_side выбирает "другую"
сторону из all_parties (результат Step 3), чтобы run_pipeline_auto_1 мог
второй раз прогнать _resolve_side_anchors для неё. Упрощение до 2 сторон
(multi-signer >2 отложен).
"""
from __future__ import annotations

from signfinder.pipeline.auto1 import _build_counterparty_side


OUR_SIDE = {
    "legal_entity": "Romashka LLC",
    "roles": ["Lessor"],
    "signer": "Ivanov I.I.",
    "confidence": 0.9,
    "all_parties": [
        {"legal_entity": "Romashka LLC", "role": "Lessor", "signer": "Ivanov I.I."},
        {"legal_entity": "Lutik LLC", "role": "Lessee", "signer": "Petrov P.P."},
    ],
}


def test_finds_other_party_by_legal_entity():
    cp = _build_counterparty_side(OUR_SIDE)
    assert cp is not None
    assert cp["legal_entity"] == "Lutik LLC"
    assert cp["roles"] == ["Lessee"]
    assert cp["signer"] == "Petrov P.P."


def test_all_parties_passed_through_unchanged_for_downstream_exclusion():
    """Контрагентский dict должен нести тот же all_parties — иначе
    downstream (_build_other_side_names/run_step5) при вызове ДЛЯ
    контрагента не сможет исключить НАШУ сторону из его собственных
    паттернов/матчей."""
    cp = _build_counterparty_side(OUR_SIDE)
    assert cp["all_parties"] == OUR_SIDE["all_parties"]


def test_skips_party_matching_our_legal_entity():
    side = {
        "legal_entity": "Romashka LLC",
        "roles": [],
        "signer": "",
        "all_parties": [
            {"legal_entity": "Romashka LLC", "role": "", "signer": ""},
            {"legal_entity": "Lutik LLC", "role": "", "signer": "Petrov P.P."},
        ],
    }
    cp = _build_counterparty_side(side)
    assert cp["legal_entity"] == "Lutik LLC"


def test_skips_party_matching_our_role():
    """Если у контрагента совпадает роль с нашей (напр. LLM спутала стороны
    местами в all_parties) — не выбираем его как 'другую' сторону."""
    side = {
        "legal_entity": "",
        "roles": ["Lessor"],
        "signer": "",
        "all_parties": [
            {"legal_entity": "", "role": "Lessor", "signer": "Someone"},
            {"legal_entity": "Lutik LLC", "role": "Lessee", "signer": "Petrov P.P."},
        ],
    }
    cp = _build_counterparty_side(side)
    assert cp["legal_entity"] == "Lutik LLC"


def test_no_other_party_returns_none():
    side = {
        "legal_entity": "Romashka LLC",
        "roles": ["Lessor"],
        "signer": "Ivanov I.I.",
        "all_parties": [
            {"legal_entity": "Romashka LLC", "role": "Lessor", "signer": "Ivanov I.I."},
        ],
    }
    assert _build_counterparty_side(side) is None


def test_empty_all_parties_returns_none():
    side = {"legal_entity": "Romashka LLC", "roles": [], "signer": "", "all_parties": []}
    assert _build_counterparty_side(side) is None


def test_missing_all_parties_key_returns_none():
    side = {"legal_entity": "Romashka LLC", "roles": [], "signer": ""}
    assert _build_counterparty_side(side) is None


def test_party_with_no_identifying_fields_skipped():
    """Пустая запись в all_parties (ни legal_entity, ни role) — не годится
    как контрагент, даже если технически 'не наша'."""
    side = {
        "legal_entity": "Romashka LLC",
        "roles": [],
        "signer": "",
        "all_parties": [
            {"legal_entity": "Romashka LLC", "role": "", "signer": ""},
            {"legal_entity": "", "role": "", "signer": ""},
            {"legal_entity": "Lutik LLC", "role": "Lessee", "signer": ""},
        ],
    }
    cp = _build_counterparty_side(side)
    assert cp["legal_entity"] == "Lutik LLC"


def test_role_only_party_accepted():
    """Контрагент без юрлица, только с ролью (напр. физлицо) — валиден."""
    side = {
        "legal_entity": "Romashka LLC",
        "roles": ["Lessor"],
        "signer": "",
        "all_parties": [
            {"legal_entity": "Romashka LLC", "role": "Lessor", "signer": ""},
            {"legal_entity": "", "role": "Tenant", "signer": "Petrov P.P."},
        ],
    }
    cp = _build_counterparty_side(side)
    assert cp is not None
    assert cp["legal_entity"] == ""
    assert cp["roles"] == ["Tenant"]
    assert cp["signer"] == "Petrov P.P."


def test_non_dict_entries_in_all_parties_ignored():
    side = {
        "legal_entity": "Romashka LLC",
        "roles": [],
        "signer": "",
        "all_parties": ["not a dict", {"legal_entity": "Lutik LLC", "role": "Lessee", "signer": ""}],
    }
    cp = _build_counterparty_side(side)
    assert cp["legal_entity"] == "Lutik LLC"
