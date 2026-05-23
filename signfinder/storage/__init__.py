"""Storage abstraction для SignFinder.

Backends: LocalFilesystemStorage (on-prem), GCSStorage (cloud).
Выбор через factory.create_storage() или явное создание.
"""
from signfinder.storage.base import StorageBackend
from signfinder.storage.factory import create_storage
from signfinder.storage.local import LocalFilesystemStorage

__all__ = [
    "StorageBackend",
    "LocalFilesystemStorage",
    "create_storage",
]

# GCSStorage импортируется явно при необходимости — чтобы не падать при
# отсутствии google-cloud-storage в on-prem установке.
