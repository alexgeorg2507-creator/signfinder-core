"""Матчинг нового документа с реестром шаблонов.

FIX v1.9: _simhash_similarity — decimal-first парсинг, fixed 64-bit.
FIX v1.9.2: find_matching_templates:
  - При score >= PERFECT_SCORE_THRESHOLD (0.99) коллизию игнорировать —
    документ идентичен шаблону, применять нужно однозначно.
  - Тайбрейк при равных score: сортировка по created_at DESC
    (новее = приоритетнее). Это решает вопрос «какой из нескольких применять».
  - Fallback в load_config: читаем оба пути (traffic_light_config.json
    и settings/traffic_light.json) т.к. API и core используют разные ключи.
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Optional

from signfinder.storage.base import StorageBackend
from signfinder.templates.models import MatcherResult, MatchResult
from signfinder.templates.storage import list_templates
from signfinder.traffic_light import TrafficLightConfig, classify, load_config

# Если best_score >= этого порога — коллизию игнорировать, применять best.
PERFECT_SCORE_THRESHOLD = 0.99


# ── Публичный API ─────────────────────────────────────────────────────────────

def find_matching_templates(
    doc,
    language: str,
    storage: StorageBackend,
    our_synonyms: Optional[dict] = None,
    fingerprint: Optional[dict] = None,
    config: Optional[TrafficLightConfig] = None,
) -> MatcherResult:
    """Главная функция матчинга."""
    # 1. Fingerprint
    if fingerprint is None:
        try:
            from signfinder.fingerprint import compute_fingerprint
            fingerprint = compute_fingerprint(doc, language)
        except Exception as e:
            sys.stderr.write(f"[template_matcher] compute_fingerprint: {e}\n")
            return _no_match_result("Не удалось вычислить fingerprint документа.")

    # 2. Шаблоны
    try:
        templates = list_templates(storage, language)
    except Exception as e:
        sys.stderr.write(f"[template_matcher] list_templates: {e}\n")
        return _no_match_result("Не удалось загрузить реестр шаблонов.")

    if not templates:
        return _no_match_result("В реестре нет шаблонов для языка: " + language)

    # 3. Каскадный матчинг
    candidates: list[MatchResult] = []
    for tpl in templates:
        tpl_fp = tpl.fingerprint or {}

        if not passes_quick_filter(fingerprint, tpl_fp):
            continue

        score, breakdown = compute_composite_score(fingerprint, tpl_fp)

        syn_match = _check_synonyms(our_synonyms, tpl.synonyms_used)
        if our_synonyms and not syn_match:
            score *= 0.75

        candidates.append(MatchResult(
            template_id=tpl.template_id,
            template_name=tpl.name,
            score=round(score, 4),
            score_breakdown=breakdown,
            explanation="",
            synonyms_match=syn_match,
            # Сохраняем created_at для тайбрейка
            _created_at=getattr(tpl, "created_at", "") or "",
        ))

    if not candidates:
        return _no_match_result("Похожих шаблонов не найдено (не прошли быструю отсечку).")

    # FIX v1.9.2: сортировка по score DESC, затем по created_at DESC (тайбрейк).
    # При равных score применяется самый новый шаблон.
    def _sort_key(c: MatchResult):
        # Нормализуем created_at в сопоставимую строку
        ca = getattr(c, "_created_at", "") or ""
        return (c.score, ca)

    candidates.sort(key=_sort_key, reverse=True)
    top5 = candidates[:5]
    best = top5[0]

    # Диагностика breakdown
    sys.stderr.write(
        f"[template_matcher] best={best.template_id} score={best.score} "
        f"breakdown={best.score_breakdown} created_at={getattr(best, '_created_at', '')}\n"
    )

    cfg = config or _load_config_with_fallback(storage)

    # FIX v1.9.2: при очень высоком score коллизию НЕ считаем блокером.
    # Три шаблона с score=1.0 → без фикса всегда yellow, шаблон не применяется.
    if best.score >= PERFECT_SCORE_THRESHOLD:
        has_collision = False
        sys.stderr.write(
            f"[template_matcher] perfect score {best.score} — collision ignored, "
            f"applying best ({best.template_id[:8]})\n"
        )
    else:
        has_collision = (
            len(top5) >= 2 and
            (top5[0].score - top5[1].score) < cfg.collision_delta
        )

    light = classify(
        score=best.score,
        synonyms_match=best.synonyms_match,
        has_collision=has_collision,
        config=cfg,
    )

    for c in top5:
        c.explanation = build_explanation(c, light)

    global_expl = _build_global_explanation(best, light, has_collision, len(candidates))

    return MatcherResult(
        traffic_light=light,
        best_match=best,
        all_candidates=top5,
        explanation=global_expl,
    )


def _load_config_with_fallback(storage: StorageBackend) -> TrafficLightConfig:
    """FIX v1.9.2: читаем оба пути — API и core используют разные ключи.

    API роутер settings.py сохраняет в settings/traffic_light.json.
    Функция load_config (core) читает traffic_light_config.json.
    Без фикса core всегда использовал дефолт (green_threshold=0.95).
    """
    # Пробуем сначала путь API
    try:
        data = storage.read_json("settings/traffic_light.json")
        if data:
            return TrafficLightConfig(**data)
    except Exception:
        pass
    # Потом путь core
    return load_config(storage)


# ── Score ─────────────────────────────────────────────────────────────────────

def compute_composite_score(new_fp: dict, tpl_fp: dict) -> tuple[float, dict]:
    """Композитный score: 0.4*simhash + 0.3*jaccard + 0.2*cosine + 0.1*pages."""
    simhash_sim = _simhash_similarity(
        new_fp.get("header_simhash"), tpl_fp.get("header_simhash"),
    )
    jaccard_sim = _jaccard_similarity(
        new_fp.get("section_titles", []), tpl_fp.get("section_titles", []),
    )
    cosine_sim = _cosine_chars_similarity(
        new_fp.get("chars_per_page", []), tpl_fp.get("chars_per_page", []),
    )
    page_sim = _page_count_similarity(
        new_fp.get("page_count", 0), tpl_fp.get("page_count", 0),
    )

    score = (
        0.4 * simhash_sim
        + 0.3 * jaccard_sim
        + 0.2 * cosine_sim
        + 0.1 * page_sim
    )

    breakdown = {
        "simhash": round(simhash_sim, 4),
        "jaccard": round(jaccard_sim, 4),
        "cosine_chars": round(cosine_sim, 4),
        "page_count_similarity": round(page_sim, 4),
        "composite": round(score, 4),
    }
    return score, breakdown


def passes_quick_filter(new_fp: dict, tpl_fp: dict) -> bool:
    """Быстрая отсечка."""
    new_lang = new_fp.get("language", "")
    tpl_lang = tpl_fp.get("language", "")
    if new_lang and tpl_lang and new_lang != tpl_lang:
        return False

    new_pages = new_fp.get("page_count", 0)
    tpl_pages = tpl_fp.get("page_count", 0)
    if abs(new_pages - tpl_pages) > 2:
        return False

    new_chars = new_fp.get("total_chars", 0)
    tpl_chars = tpl_fp.get("total_chars", 0)
    if tpl_chars > 0:
        ratio = new_chars / tpl_chars
        if not (0.8 <= ratio <= 1.25):
            return False

    return True


# ── Explanation ───────────────────────────────────────────────────────────────

def build_explanation(match: MatchResult, traffic_light: str) -> str:
    parts = []
    bd = match.score_breakdown

    sh = bd.get("simhash", 0.0)
    if sh >= 0.95:
        parts.append("Шапка договора почти идентична")
    elif sh >= 0.85:
        parts.append(f"Шапка договора очень похожа ({int(sh * 100)}%)")
    elif sh >= 0.70:
        parts.append(f"Шапка договора частично совпадает ({int(sh * 100)}%)")
    else:
        parts.append(f"Шапка договора отличается (совпадение {int(sh * 100)}%)")

    j = bd.get("jaccard", 0.0)
    if j >= 0.9:
        parts.append("структура разделов идентична")
    elif j >= 0.7:
        parts.append(f"структура разделов совпадает на {int(j * 100)}%")
    else:
        parts.append(f"структура разделов отличается ({int(j * 100)}% совпадения)")

    pc = bd.get("page_count_similarity", 0.0)
    if pc >= 0.95:
        parts.append("количество страниц совпадает")
    else:
        parts.append("количество страниц немного отличается")

    if not match.synonyms_match:
        parts.append("⚠ синонимы стороны в этом документе отличаются от шаблона")

    return ". ".join(parts) + "."


def _build_global_explanation(
    best: MatchResult,
    light: str,
    has_collision: bool,
    total_candidates: int,
) -> str:
    if light == "green":
        return (
            f"Шаблон «{best.template_name}» подошёл с уверенностью "
            f"{int(best.score * 100)}%. {best.explanation}"
        )
    if has_collision:
        return (
            f"Найдено {total_candidates} похожих шаблонов, "
            f"лучший score {int(best.score * 100)}% — коллизия, нужен выбор оператора."
        )
    if best.score > 0:
        return (
            f"Шаблон «{best.template_name}» похож на {int(best.score * 100)}%, "
            f"но уверенности для автоматического применения недостаточно."
        )
    return "Похожих шаблонов не найдено. Запускается полный анализ."


# ── Logging ───────────────────────────────────────────────────────────────────

def log_matching_decision(matcher_result: MatcherResult, doc_filename: str) -> None:
    from datetime import datetime, timezone
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "doc_filename": doc_filename,
        "traffic_light": matcher_result.traffic_light,
        "best_score": matcher_result.best_match.score if matcher_result.best_match else None,
        "best_template_id": matcher_result.best_match.template_id if matcher_result.best_match else None,
        "candidates_count": len(matcher_result.all_candidates),
        "explanation": matcher_result.explanation,
    }
    print(f"[TRAFFIC_LIGHT] {json.dumps(record, ensure_ascii=False)}", file=sys.stderr)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _no_match_result(explanation: str) -> MatcherResult:
    return MatcherResult(
        traffic_light="yellow",
        best_match=None,
        all_candidates=[],
        explanation=explanation,
    )


def _parse_simhash(h) -> Optional[int]:
    """Парсит simhash в int.

    FIX v1.9: decimal-first парсинг (Simhash.value всегда decimal str).
    Старый int(s, 16) интерпретировал decimal как hex → неверный XOR.
    """
    if h is None or h == "":
        return None
    if isinstance(h, bool):
        return None
    if isinstance(h, int):
        return h
    if isinstance(h, str):
        s = h.strip()
        if not s:
            return None
        try:
            return int(s, 10)
        except ValueError:
            try:
                return int(s, 16)
            except ValueError:
                return None
    return None


def _simhash_similarity(hash_a, hash_b) -> float:
    a = _parse_simhash(hash_a)
    b = _parse_simhash(hash_b)
    if a is None or b is None:
        return 0.0
    try:
        xor = a ^ b
        diff_bits = bin(xor).count("1")
        total_bits = 64  # Simhash всегда 64-битный
        return max(0.0, 1.0 - diff_bits / total_bits)
    except Exception:
        return 0.0


def _jaccard_similarity(set_a, set_b) -> float:
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    sa = set(s.lower().strip() for s in set_a if s)
    sb = set(s.lower().strip() for s in set_b if s)
    if not sa and not sb:
        return 1.0
    intersection = len(sa & sb)
    union = len(sa | sb)
    return intersection / union if union > 0 else 0.0


def _cosine_chars_similarity(vec_a: list, vec_b: list) -> float:
    if not vec_a or not vec_b:
        return 0.0
    n = max(len(vec_a), len(vec_b))
    a = _normalize([float(x) for x in vec_a] + [0.0] * (n - len(vec_a)))
    b = _normalize([float(x) for x in vec_b] + [0.0] * (n - len(vec_b)))
    dot = sum(x * y for x, y in zip(a, b))
    return max(0.0, min(1.0, dot))


def _normalize(vec: list) -> list:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        return vec
    return [x / norm for x in vec]


def _page_count_similarity(n: int, m: int) -> float:
    if n == 0 and m == 0:
        return 1.0
    denom = max(n, m)
    if denom == 0:
        return 0.0
    return 1.0 - abs(n - m) / denom


def _check_synonyms(our_synonyms: Optional[dict], tpl_synonyms: dict) -> bool:
    if not our_synonyms:
        return True
    if not tpl_synonyms:
        return False

    our_tokens = _synonym_tokens(our_synonyms)
    tpl_tokens = _synonym_tokens(tpl_synonyms)

    if not our_tokens or not tpl_tokens:
        return True

    return bool(our_tokens & tpl_tokens)


def _synonym_tokens(synonyms: dict) -> set:
    tokens = set()
    le = (synonyms.get("legal_entity") or "").strip().lower()
    if le and len(le) >= 3:
        tokens.add(le)
        for word in le.split():
            if len(word) >= 4:
                tokens.add(word)
    for role in (synonyms.get("roles") or []):
        r = (role or "").strip().lower()
        if len(r) >= 3:
            tokens.add(r)
    signer = (synonyms.get("signer") or "").strip().lower()
    if signer and len(signer) >= 3:
        tokens.add(signer)
    return tokens
