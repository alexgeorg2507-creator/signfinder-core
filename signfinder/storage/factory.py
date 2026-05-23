"""Factory для создания storage backend по конфигу или env vars."""
from __future__ import annotations

import os
from typing import Literal, Optional

from signfinder.storage.base import StorageBackend


def create_storage(
    mode: Optional[Literal["local", "gcs"]] = None,
    path: Optional[str] = None,
    bucket: Optional[str] = None,
) -> StorageBackend:
    """Factory для создания storage backend.

    Приоритеты:
      1. Явные параметры функции
      2. Env vars (STORAGE_MODE, STORAGE_PATH, GCS_BUCKET)
      3. Дефолты (local + ./signfinder_data)

    Legacy fallback: если задан GCS_BUCKET, но не STORAGE_MODE — режим gcs.
    """
    resolved_mode = mode or os.environ.get("STORAGE_MODE")
    if not resolved_mode:
        # Legacy: только GCS_BUCKET задан — gcs
        if os.environ.get("GCS_BUCKET"):
            resolved_mode = "gcs"
        else:
            resolved_mode = "local"

    resolved_mode = resolved_mode.lower()

    if resolved_mode == "local":
        resolved_path = path or os.environ.get("STORAGE_PATH", "./signfinder_data")
        from signfinder.storage.local import LocalFilesystemStorage
        return LocalFilesystemStorage(resolved_path)

    if resolved_mode == "gcs":
        resolved_bucket = bucket or os.environ.get("GCS_BUCKET", "signfinder-config")
        from signfinder.storage.gcs import GCSStorage
        return GCSStorage(resolved_bucket)

    raise ValueError(f"Unknown STORAGE_MODE: {resolved_mode}")
