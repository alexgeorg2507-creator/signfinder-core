"""Абстракция хранилища данных SignFinder."""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Абстракция хранилища данных SignFinder.

    Поддерживает чтение/запись бинарных файлов, JSON-конфигов и
    листинг по префиксу. Все пути относительные от корня хранилища.

    Backends:
      - LocalFilesystemStorage — для Desktop on-prem
      - GCSStorage — для Cloud SaaS
    """

    def read_bytes(self, path: str) -> Optional[bytes]:
        """Читает файл целиком. None если файла нет."""
        ...

    def write_bytes(self, path: str, data: bytes) -> None:
        """Записывает файл. Создаёт промежуточные директории при необходимости."""
        ...

    def exists(self, path: str) -> bool:
        """True если файл существует."""
        ...

    def delete(self, path: str) -> bool:
        """Удаляет файл. True если был удалён, False если не существовал."""
        ...

    def list_prefix(self, prefix: str) -> list[str]:
        """Список путей файлов начинающихся с префикса."""
        ...

    def read_json(self, path: str) -> Optional[dict]:
        """Читает JSON. None если файла нет."""
        ...

    def write_json(self, path: str, data: dict) -> None:
        """Записывает JSON с indent=2 и ensure_ascii=False."""
        ...

    def read_text(self, path: str) -> Optional[str]:
        """Читает текстовый файл (UTF-8). None если файла нет."""
        ...

    def write_text(self, path: str, content: str) -> None:
        """Записывает текстовый файл (UTF-8)."""
        ...
