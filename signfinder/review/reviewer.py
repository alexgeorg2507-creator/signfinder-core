"""Ревью договора через LLM (pre-flight check, v1.20)."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from signfinder.llm import LLMClient, LLMError
from signfinder.prompts.contract_review import format_contract_review
from signfinder.review.jurisdictions import get_jurisdiction


@dataclass
class ReviewFinding:
    axis: str
    severity: str          # critical | warning | info
    note: str
    clause: Optional[str] = None


@dataclass
class ReviewResult:
    traffic_light: str               # green | yellow | red
    summary: str = ""
    findings: list = field(default_factory=list)
    error: Optional[str] = None
    truncated: bool = False          # был ли договор обрезан
    raw: Optional[str] = None        # сырой ответ LLM для debug

    def to_dict(self) -> dict:
        return {
            "traffic_light": self.traffic_light,
            "summary": self.summary,
            "findings": [
                {"axis": f.axis, "severity": f.severity,
                 "note": f.note, "clause": f.clause}
                for f in self.findings
            ],
            "error": self.error,
            "truncated": self.truncated,
        }


# Лимит текста договора в промпт (символов). ~60K покрывает 25-30 страниц.
_MAX_CONTRACT_CHARS = 60000
# Порог страниц для предупреждения пользователю.
_PAGES_WARN_THRESHOLD = 50


def _truncate_head_tail(text: str, limit: int = _MAX_CONTRACT_CHARS) -> tuple[str, bool]:
    """Умная обрезка ГОЛОВА+ХВОСТ для больших договоров.

    Для pre-flight критичны и начало (стороны, предмет), и конец (расчёты,
    ответственность, подписи). Тупой срез [:limit] убил бы конец.
    Берём 60% с начала + 40% с конца, середину пропускаем.

    Returns:
        (обрезанный_текст, был_ли_обрезан)
    """
    if len(text) <= limit:
        return text, False
    head = int(limit * 0.6)
    tail = limit - head
    marker = "\n\n[...середина договора пропущена для ревью...]\n\n"
    return text[:head] + marker + text[-tail:], True


def review_contract(
    contract_text: str,
    language: str,
    llm: LLMClient,
    page_count: int = 0,
    max_tokens: int = 1500,
) -> ReviewResult:
    """Проверить договор через LLM, вернуть структурированное ревью.

    Args:
        contract_text: полный текст договора
        language: язык (ru/en/pl/mk или составной 'mk, en')
        llm: LLM-клиент
        page_count: число страниц (для предупреждения о больших документах)
        max_tokens: лимит ответа

    Returns:
        ReviewResult. При ошибке LLM — traffic_light='yellow', error заполнен
        (НЕ блокируем подпись — ревью информационное).
    """
    if not contract_text or not contract_text.strip():
        return ReviewResult(traffic_light="yellow", error="empty contract text")

    text, truncated = _truncate_head_tail(contract_text)

    jur_name, jur_context = get_jurisdiction(language)
    prompt = format_contract_review(
        contract_text=text,
        language=language,
        jurisdiction_name=jur_name,
        jurisdiction_context=jur_context,
    )

    try:
        raw = llm.complete(prompt, max_tokens=max_tokens)
    except LLMError as e:
        sys.stderr.write(f"[review] LLM error: {e}\n")
        return ReviewResult(traffic_light="yellow", error=str(e), truncated=truncated)

    result = _parse_review(raw)
    result.truncated = truncated

    # Предупреждение для очень больших документов — добавляем info-finding
    if page_count > _PAGES_WARN_THRESHOLD:
        result.findings.insert(0, ReviewFinding(
            axis="other",
            severity="info",
            note=(f"Документ большой ({page_count} стр.) — ревью выполнено по "
                  f"началу и концу договора, середина пропущена."),
            clause=None,
        ))

    return result


def _parse_review(raw: str) -> ReviewResult:
    """Распарсить JSON-ответ LLM в ReviewResult."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        sys.stderr.write(f"[review] invalid JSON: {e}\n")
        return ReviewResult(traffic_light="yellow",
                            error=f"invalid JSON: {e}", raw=raw[:300])

    findings = []
    for f in data.get("findings", []):
        if not isinstance(f, dict):
            continue
        findings.append(ReviewFinding(
            axis=f.get("axis", "other"),
            severity=f.get("severity", "info"),
            note=f.get("note", ""),
            clause=f.get("clause"),
        ))

    tl = data.get("overall", "yellow")
    if tl not in ("green", "yellow", "red"):
        tl = "yellow"

    return ReviewResult(
        traffic_light=tl,
        summary=data.get("summary", ""),
        findings=findings,
        raw=raw[:500],
    )
