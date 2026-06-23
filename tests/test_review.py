"""Тесты pre-flight ревью (v1.20)."""
import json
from signfinder.review import review_contract, ReviewResult
from signfinder.review.jurisdictions import get_jurisdiction
from signfinder.review.reviewer import _truncate_head_tail, _MAX_CONTRACT_CHARS


def test_jurisdiction_ru():
    name, ctx = get_jurisdiction("ru")
    assert "Росси" in name or "РФ" in name
    assert ctx


def test_jurisdiction_composite_lang():
    name, ctx = get_jurisdiction("mk, en")
    assert "Македон" in name or "Makedon" in name


def test_jurisdiction_unknown_fallback():
    name, ctx = get_jurisdiction("fr")
    assert name


def test_truncate_short_text_untouched():
    text = "короткий договор"
    result, truncated = _truncate_head_tail(text)
    assert result == text
    assert truncated is False


def test_truncate_long_text_head_tail():
    """Длинный текст обрезается голова+хвост, начало и конец сохранены."""
    head_marker = "НАЧАЛО_ДОГОВОРА"
    tail_marker = "КОНЕЦ_ДОГОВОРА_ПОДПИСИ"
    middle = "x" * (_MAX_CONTRACT_CHARS * 2)
    text = head_marker + middle + tail_marker
    result, truncated = _truncate_head_tail(text)
    assert truncated is True
    assert head_marker in result        # начало сохранено
    assert tail_marker in result        # конец сохранён
    assert len(result) < len(text)      # короче оригинала
    assert "пропущена" in result        # маркер пропуска


def test_review_empty_text():
    result = review_contract("", "ru", llm=None)
    assert result.traffic_light == "yellow"
    assert result.error


def test_review_with_mock_llm(mock_llm_review):
    result = review_contract("Договор аренды между сторонами...", "ru", mock_llm_review)
    assert result.traffic_light in ("green", "yellow", "red")
    assert isinstance(result.findings, list)
    assert len(result.findings) >= 1


def test_review_large_doc_warning(mock_llm_review):
    """Документ >50 страниц → info-finding о частичном ревью."""
    result = review_contract("Договор...", "ru", mock_llm_review, page_count=60)
    notes = [f.note for f in result.findings]
    assert any("60 стр" in n or "большой" in n for n in notes)


def test_review_llm_error_returns_yellow(mock_llm_error):
    """Ошибка LLM → yellow + error, подпись НЕ блокируется."""
    result = review_contract("Договор...", "ru", mock_llm_error)
    assert result.traffic_light == "yellow"
    assert result.error
