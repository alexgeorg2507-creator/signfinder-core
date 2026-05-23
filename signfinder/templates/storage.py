"""CRUD для DocumentTemplate через StorageBackend.

ПЕРЕНОС из core/template_storage.py с заменой прямых GCS-вызовов
на storage abstraction. Все функции принимают storage: StorageBackend
как первый параметр (вместо чтения env вара GCS_BUCKET внутри).
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from signfinder.storage.base import StorageBackend
from signfinder.templates.models import DocumentTemplate
from signfinder.utils.logging import get_logger

logger = get_logger(__name__)

_TEMPLATES_PREFIX = "templates"
_ARCHIVE_PREFIX = "templates/_archive"


# ── Path helpers ──────────────────────────────────────────────────────────────

def _blob_path(template_id: str) -> str:
    return f"{_TEMPLATES_PREFIX}/{template_id}.json"


def _archive_blob_path(template_id: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{_ARCHIVE_PREFIX}/{template_id}_{ts}.json"


# ── CRUD ──────────────────────────────────────────────────────────────────────

def save_template(storage: StorageBackend, template: DocumentTemplate) -> str:
    """Сохраняет шаблон. Возвращает template_id."""
    try:
        storage.write_json(_blob_path(template.template_id), asdict(template))
    except Exception as e:
        logger.error("save_template failed: %s", e)
        sys.stderr.write(f"[template_storage] save_template: {e}\n")
        raise
    return template.template_id


def load_template(storage: StorageBackend, template_id: str) -> Optional[DocumentTemplate]:
    """Читает шаблон по ID. None если не найден."""
    try:
        data = storage.read_json(_blob_path(template_id))
        if data is None:
            return None
        return DocumentTemplate(**data)
    except Exception as e:
        logger.error("load_template %s failed: %s", template_id, e)
        sys.stderr.write(f"[template_storage] load_template {template_id}: {e}\n")
        return None


def list_templates(
    storage: StorageBackend,
    language: Optional[str] = None,
) -> list[DocumentTemplate]:
    """Список всех шаблонов. Опционально с фильтром по языку.

    Исключает _archive/ префикс.
    """
    templates: list[DocumentTemplate] = []
    try:
        for path in storage.list_prefix(f"{_TEMPLATES_PREFIX}/"):
            if not path.endswith(".json"):
                continue
            if "/_archive/" in path:
                continue
            try:
                data = storage.read_json(path)
                if data is None:
                    continue
                t = DocumentTemplate(**data)
                if language is None or t.language == language:
                    templates.append(t)
            except Exception as e:
                sys.stderr.write(f"[template_storage] list skip {path}: {e}\n")
    except Exception as e:
        logger.error("list_templates failed: %s", e)
        sys.stderr.write(f"[template_storage] list_templates: {e}\n")
    return templates


def delete_template(storage: StorageBackend, template_id: str) -> bool:
    """Удаляет шаблон. Бэкап в _archive/ перед удалением."""
    try:
        path = _blob_path(template_id)
        content = storage.read_bytes(path)
        if content is None:
            return False
        # Архивируем перед удалением
        storage.write_bytes(_archive_blob_path(template_id), content)
        storage.delete(path)
        return True
    except Exception as e:
        logger.error("delete_template %s failed: %s", template_id, e)
        sys.stderr.write(f"[template_storage] delete_template {template_id}: {e}\n")
        return False


# ── Factory ──────────────────────────────────────────────────────────────────

def generate_template_name(language: str, synonyms: Optional[dict] = None) -> str:
    """Имя по схеме: pipelineAuto1_YYYY-MM-DD_HHMM_<lang>[_<тип>]"""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    name = f"pipelineAuto1_{ts}_{language}"
    if synonyms:
        doc_type = synonyms.get("doc_type") or synonyms.get("legal_entity", "")
        if doc_type:
            safe = str(doc_type)[:20].replace(" ", "_")
            name = f"{name}_{safe}"
    return name


def new_template(
    language: str,
    anchors: list,
    fingerprint: dict,
    synonyms_used: Optional[dict] = None,
    created_by: str = "pipeline_auto_1",
) -> DocumentTemplate:
    """Фабрика — создаёт новый DocumentTemplate с uuid и текущим timestamp."""
    synonyms_used = synonyms_used or {}
    return DocumentTemplate(
        template_id=uuid4().hex,
        name=generate_template_name(language, synonyms_used),
        language=language,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by=created_by,
        fingerprint=fingerprint,
        anchors=anchors,
        synonyms_used=synonyms_used,
    )


# ── Stats и расширение ──────────────────────────────────────────────────────

def update_usage_stats(
    storage: StorageBackend,
    template_id: str,
    event: str,  # "applied" | "confirmed" | "rejected"
) -> None:
    """Обновляет статистику использования шаблона."""
    template = load_template(storage, template_id)
    if template is None:
        sys.stderr.write(f"[template_storage] update_usage_stats: {template_id} not found\n")
        return

    stats = template.usage_stats or {
        "times_applied": 0,
        "times_confirmed": 0,
        "times_rejected": 0,
        "last_used": None,
    }

    if event == "applied":
        stats["times_applied"] = stats.get("times_applied", 0) + 1
        stats["last_used"] = datetime.now(timezone.utc).isoformat()
    elif event == "confirmed":
        stats["times_confirmed"] = stats.get("times_confirmed", 0) + 1
    elif event == "rejected":
        stats["times_rejected"] = stats.get("times_rejected", 0) + 1
    else:
        sys.stderr.write(f"[template_storage] update_usage_stats: unknown event '{event}'\n")
        return

    template.usage_stats = stats
    try:
        save_template(storage, template)
    except Exception as e:
        sys.stderr.write(f"[template_storage] update_usage_stats save: {e}\n")


def add_anchors_to_template(
    storage: StorageBackend,
    template_id: str,
    new_anchors: list,
    increment_version: bool = False,
) -> Optional[str]:
    """Добавляет якоря к существующему шаблону.

    increment_version=False: обновляет шаблон на месте.
    increment_version=True:  создаёт новую запись с суффиксом _v2, _v3 и т.д.
    """
    import re as _re

    template = load_template(storage, template_id)
    if template is None:
        sys.stderr.write(f"[template_storage] add_anchors_to_template: {template_id} not found\n")
        return None

    def _anchor_id(a):
        return a.get("id") if isinstance(a, dict) else getattr(a, "id", None)

    def _to_dict(a):
        return a if isinstance(a, dict) else asdict(a)

    if not increment_version:
        existing_ids = {_anchor_id(a) for a in template.anchors}
        for anchor in new_anchors:
            d = _to_dict(anchor)
            if _anchor_id(d) not in existing_ids:
                template.anchors.append(d)
                existing_ids.add(_anchor_id(d))
        try:
            save_template(storage, template)
        except Exception as e:
            sys.stderr.write(f"[template_storage] add_anchors_to_template save: {e}\n")
            return None
        return template_id

    base_name = _re.sub(r"_v\d+$", "", template.name)
    existing_names = {t.name for t in list_templates(storage, template.language)}
    version = 2
    while f"{base_name}_v{version}" in existing_names:
        version += 1

    all_anchors = list(template.anchors)
    existing_ids = {_anchor_id(a) for a in all_anchors}
    for anchor in new_anchors:
        d = _to_dict(anchor)
        if _anchor_id(d) not in existing_ids:
            all_anchors.append(d)
            existing_ids.add(_anchor_id(d))

    new_tpl = DocumentTemplate(
        template_id=uuid4().hex,
        name=f"{base_name}_v{version}",
        language=template.language,
        created_at=datetime.now(timezone.utc).isoformat(),
        created_by="manual_enrichment",
        fingerprint=template.fingerprint,
        anchors=all_anchors,
        synonyms_used=template.synonyms_used,
    )
    try:
        return save_template(storage, new_tpl)
    except Exception as e:
        sys.stderr.write(f"[template_storage] add_anchors_to_template new version: {e}\n")
        return None
