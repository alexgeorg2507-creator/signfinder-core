"""Storage backend для Google Cloud Storage (Cloud SaaS).

Импорт google.cloud — lazy, чтобы пакет можно было установить без
google-cloud-storage extras для on-prem варианта.
"""
from __future__ import annotations

import json
from typing import Optional

from signfinder.utils.logging import get_logger

logger = get_logger(__name__)


class GCSStorage:
    """Storage backend для Google Cloud Storage.

    Использует Application Default Credentials (на Cloud Run работает
    автоматически через служебный аккаунт).
    """

    def __init__(self, bucket_name: str):
        try:
            from google.cloud import storage  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "google-cloud-storage не установлен. "
                "Установите: pip install signfinder-core[gcs]"
            ) from e

        self.client = storage.Client()
        self.bucket = self.client.bucket(bucket_name)
        self.bucket_name = bucket_name
        logger.info("GCSStorage initialised on bucket %s", bucket_name)

    # ── Bytes ──────────────────────────────────────────────────────────────

    def read_bytes(self, path: str) -> Optional[bytes]:
        blob = self.bucket.blob(path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        blob = self.bucket.blob(path)
        blob.upload_from_string(data)

    # ── Text ───────────────────────────────────────────────────────────────

    def read_text(self, path: str) -> Optional[str]:
        blob = self.bucket.blob(path)
        if not blob.exists():
            return None
        return blob.download_as_text()

    def write_text(self, path: str, content: str) -> None:
        blob = self.bucket.blob(path)
        blob.upload_from_string(content, content_type="text/plain; charset=utf-8")

    # ── Existence / deletion ───────────────────────────────────────────────

    def exists(self, path: str) -> bool:
        return self.bucket.blob(path).exists()

    def delete(self, path: str) -> bool:
        blob = self.bucket.blob(path)
        if not blob.exists():
            return False
        blob.delete()
        return True

    # ── Listing ────────────────────────────────────────────────────────────

    def list_prefix(self, prefix: str) -> list[str]:
        return sorted(
            blob.name for blob in self.client.list_blobs(self.bucket, prefix=prefix)
        )

    # ── JSON ───────────────────────────────────────────────────────────────

    def read_json(self, path: str) -> Optional[dict]:
        raw = self.read_text(path)
        if raw is None:
            return None
        return json.loads(raw)

    def write_json(self, path: str, data: dict) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        blob = self.bucket.blob(path)
        blob.upload_from_string(content, content_type="application/json")
