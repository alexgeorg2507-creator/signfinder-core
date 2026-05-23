"""Storage backend для локального filesystem (Desktop on-prem, dev)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from signfinder.utils.logging import get_logger

logger = get_logger(__name__)


class LocalFilesystemStorage:
    """Storage backend для локального filesystem.

    Все пути относительные от root_path. Защита от path traversal.
    """

    def __init__(self, root_path: str):
        self.root = Path(root_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        logger.info("LocalFilesystemStorage initialised at %s", self.root)

    # ── Path safety ────────────────────────────────────────────────────────

    def _full_path(self, path: str) -> Path:
        # Защита от path traversal: после resolve итоговый путь должен быть
        # внутри self.root
        full = (self.root / path).resolve()
        try:
            full.relative_to(self.root)
        except ValueError:
            raise ValueError(f"Path traversal blocked: {path}")
        return full

    # ── Bytes ──────────────────────────────────────────────────────────────

    def read_bytes(self, path: str) -> Optional[bytes]:
        full = self._full_path(path)
        if not full.exists():
            return None
        return full.read_bytes()

    def write_bytes(self, path: str, data: bytes) -> None:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_bytes(data)

    # ── Text ───────────────────────────────────────────────────────────────

    def read_text(self, path: str) -> Optional[str]:
        full = self._full_path(path)
        if not full.exists():
            return None
        return full.read_text(encoding="utf-8")

    def write_text(self, path: str, content: str) -> None:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    # ── Existence / deletion ───────────────────────────────────────────────

    def exists(self, path: str) -> bool:
        return self._full_path(path).exists()

    def delete(self, path: str) -> bool:
        full = self._full_path(path)
        if not full.exists():
            return False
        full.unlink()
        return True

    # ── Listing ────────────────────────────────────────────────────────────

    def list_prefix(self, prefix: str) -> list[str]:
        """Все файлы относительно self.root начинающиеся с prefix.

        prefix трактуется как путь, не как posix-glob.
        """
        # Разрешаем prefix без существования файла
        base = (self.root / prefix).parent if "/" in prefix else self.root
        if not base.exists():
            return []

        result: list[str] = []
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(self.root).as_posix()
            except ValueError:
                continue
            if rel.startswith(prefix):
                result.append(rel)
        return sorted(result)

    # ── JSON ───────────────────────────────────────────────────────────────

    def read_json(self, path: str) -> Optional[dict]:
        raw = self.read_text(path)
        if raw is None:
            return None
        return json.loads(raw)

    def write_json(self, path: str, data: dict) -> None:
        content = json.dumps(data, ensure_ascii=False, indent=2)
        self.write_text(path, content)
