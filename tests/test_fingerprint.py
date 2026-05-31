"""Тесты fingerprint + similarity-функций матчера (v1.15)."""
from __future__ import annotations

import io

import fitz
import pytest

from signfinder.fingerprint import (
    compute_fingerprint,
    extract_section_titles,
    find_header_page,
)
from signfinder.templates.matcher import (
    _jaccard_similarity,
    _cosine_chars_similarity,
    _page_count_similarity,
    _simhash_similarity,
    _parse_simhash,
    compute_composite_score,
)


# ── Фикстуры ─────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_pdf_doc():
    """Простой 2-страничный PDF (Latin-текст)."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "LEASE AGREEMENT No 123/2026", fontsize=12)
    p1.insert_text(
        (50, 80),
        "Romashka LLC, hereinafter referred to as the Lessor, "
        "represented by Director Ivanov I.I., on the one hand, and "
        "Lutik LLC, hereinafter referred to as the Lessee, "
        "represented by Director Petrov P.P., on the other hand, "
        "have agreed as follows:",
        fontsize=10,
    )
    p1.insert_text((50, 160), "1. Subject Of The Agreement", fontsize=10)
    p1.insert_text((50, 180), "1.1. The Lessor transfers to the Lessee for temporary use...", fontsize=10)

    p2 = doc.new_page()
    p2.insert_text((50, 50), "Section 2. Obligations", fontsize=10)
    p2.insert_text((50, 70), "Annex No 1", fontsize=10)
    p2.insert_text((50, 90), "Signatures:", fontsize=10)
    p2.insert_text((50, 110), "Lessor: _________________ /Ivanov I.I./", fontsize=10)
    p2.insert_text((50, 130), "Lessee: _________________ /Petrov P.P./", fontsize=10)

    buf = io.BytesIO()
    doc.save(buf)
    pdf_bytes = buf.getvalue()
    doc.close()
    return fitz.open(stream=pdf_bytes, filetype="pdf")


# ── compute_fingerprint ───────────────────────────────────────────────────────

def test_compute_fingerprint_basic(sample_pdf_doc):
    fp = compute_fingerprint(sample_pdf_doc, language="en")
    assert fp["page_count"] == 2
    assert fp["total_chars"] > 100
    assert len(fp["chars_per_page"]) == 2
    assert fp["language"] == "en"
    assert isinstance(fp["section_titles"], list)


def test_find_header_page_finds_preamble(sample_pdf_doc):
    idx = find_header_page(sample_pdf_doc, "en")
    assert idx == 0


def test_find_header_page_fallback(sample_pdf_doc):
    idx = find_header_page(sample_pdf_doc, "xx")
    assert idx == 0


def test_extract_section_titles(sample_pdf_doc):
    titles = extract_section_titles(sample_pdf_doc)
    titles_lower = [t.lower() for t in titles]
    assert any("subject" in t for t in titles_lower)
    assert any("annex" in t for t in titles_lower)


def test_header_simhash_is_string(sample_pdf_doc):
    fp = compute_fingerprint(sample_pdf_doc, language="en")
    assert isinstance(fp["header_simhash"], str)


def test_fingerprint_empty_pdf():
    doc = fitz.open()
    doc.new_page()
    fp = compute_fingerprint(doc, language="ru")
    assert fp["page_count"] == 1
    assert fp["section_titles"] == []
    doc.close()


# ── _parse_simhash — REGRESSION: decimal-first, не hex ───────────────────────

def test_parse_simhash_decimal_string():
    """Большое десятичное число должно парситься как decimal, не hex."""
    # 10-значное число: в hex тоже валидно, decimal-first должен выиграть
    val = _parse_simhash("12345678901234")
    assert val == 12345678901234


def test_parse_simhash_decimal_matches_int():
    """str(int) → round-trip."""
    original = 9876543210
    val = _parse_simhash(str(original))
    assert val == original


def test_parse_simhash_64bit_no_overflow():
    """64-bit значение не должно давать ошибку."""
    big = 2**63 - 1  # MAX SIGNED 64-bit
    val = _parse_simhash(str(big))
    assert val == big


def test_parse_simhash_empty_returns_none():
    assert _parse_simhash("") is None
    assert _parse_simhash(None) is None


def test_parse_simhash_int_passthrough():
    assert _parse_simhash(42) == 42


def test_simhash_similarity_identical():
    h = "12345678901"
    assert _simhash_similarity(h, h) == 1.0


def test_simhash_similarity_different():
    a = "0"   # 64 нулей
    b = str(2**64 - 1)  # 64 единицы → все биты отличаются
    sim = _simhash_similarity(a, b)
    assert sim == 0.0


def test_simhash_similarity_one_none():
    assert _simhash_similarity(None, "123") == 0.0
    assert _simhash_similarity("123", None) == 0.0


# ── jaccard ───────────────────────────────────────────────────────────────────

def test_jaccard_known_value():
    """jaccard({"a","b","c"}, {"b","c","d"}) = 2/4 = 0.5."""
    sim = _jaccard_similarity(["a", "b", "c"], ["b", "c", "d"])
    assert abs(sim - 0.5) < 1e-9


def test_jaccard_identical():
    assert _jaccard_similarity(["a", "b"], ["a", "b"]) == 1.0


def test_jaccard_disjoint():
    assert _jaccard_similarity(["a", "b"], ["c", "d"]) == 0.0


def test_jaccard_both_empty():
    assert _jaccard_similarity([], []) == 1.0


def test_jaccard_one_empty():
    assert _jaccard_similarity(["a"], []) == 0.0


# ── cosine chars ─────────────────────────────────────────────────────────────

def test_cosine_identical_vectors():
    vec = [100.0, 200.0, 150.0]
    assert abs(_cosine_chars_similarity(vec, vec) - 1.0) < 1e-9


def test_cosine_zero_vector():
    assert _cosine_chars_similarity([0, 0, 0], [1, 2, 3]) == 0.0


def test_cosine_empty():
    assert _cosine_chars_similarity([], []) == 0.0


def test_cosine_different_length():
    a = [100.0, 200.0]
    b = [100.0, 200.0, 0.0]
    # После паддинга — идентичны
    sim = _cosine_chars_similarity(a, b)
    assert abs(sim - 1.0) < 1e-6


# ── page_count ────────────────────────────────────────────────────────────────

def test_page_count_same():
    assert _page_count_similarity(5, 5) == 1.0


def test_page_count_diff_1():
    sim = _page_count_similarity(5, 6)
    assert abs(sim - (1.0 - 1 / 6)) < 1e-9


def test_page_count_zero_both():
    assert _page_count_similarity(0, 0) == 1.0


def test_page_count_diff_large():
    sim = _page_count_similarity(2, 10)
    assert sim < 0.5


# ── compute_composite_score с весами 0.40/0.30/0.20/0.10 ────────────────────

def test_composite_all_ones():
    fp = {
        "header_simhash": "100",
        "section_titles": ["A"],
        "chars_per_page": [100.0],
        "page_count": 1,
    }
    score, bd = compute_composite_score(fp, fp)
    assert abs(score - 1.0) < 1e-6
    assert abs(bd["composite"] - 1.0) < 1e-6


def test_composite_weights_sum():
    """Веса: simhash=0.4, jaccard=0.3, cosine=0.2, page=0.1 → итого 1.0."""
    # При одинаковых fp все компоненты = 1.0 → composite = 1.0
    fp = {
        "header_simhash": "42",
        "section_titles": ["X", "Y"],
        "chars_per_page": [500.0, 300.0],
        "page_count": 2,
    }
    score, bd = compute_composite_score(fp, fp)
    expected = 0.4 * bd["simhash"] + 0.3 * bd["jaccard"] + 0.2 * bd["cosine_chars"] + 0.1 * bd["page_count_similarity"]
    assert abs(score - expected) < 1e-9


def test_composite_page_mismatch_lowers_score():
    fp_a = {"header_simhash": "42", "section_titles": ["A"], "chars_per_page": [500.0], "page_count": 5}
    fp_b = {"header_simhash": "42", "section_titles": ["A"], "chars_per_page": [500.0], "page_count": 10}
    score, _ = compute_composite_score(fp_a, fp_b)
    assert score < 1.0
