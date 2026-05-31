"""Тесты storage abstraction (v1.15).

Включает регрессию: .read() / .write() не существуют → AttributeError.
"""
from __future__ import annotations

import pytest

from signfinder.storage import LocalFilesystemStorage, create_storage


def test_local_storage_write_read_bytes(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write_bytes("templates/test.json", b'{"key": "value"}')
    data = storage.read_bytes("templates/test.json")
    assert data == b'{"key": "value"}'


def test_local_storage_write_read_json(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write_json("config.json", {"foo": "bar", "rus": "кириллица"})
    data = storage.read_json("config.json")
    assert data == {"foo": "bar", "rus": "кириллица"}


def test_local_storage_read_nonexistent_returns_none(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    assert storage.read_bytes("missing.json") is None
    assert storage.read_json("missing.json") is None
    assert storage.read_text("missing.txt") is None


def test_local_storage_exists_and_delete(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write_bytes("a/b/c.dat", b"x")
    assert storage.exists("a/b/c.dat") is True
    assert storage.delete("a/b/c.dat") is True
    assert storage.exists("a/b/c.dat") is False
    assert storage.delete("a/b/c.dat") is False


def test_local_storage_list_prefix(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    storage.write_json("templates/t1.json", {"id": 1})
    storage.write_json("templates/t2.json", {"id": 2})
    storage.write_json("settings.json", {"other": True})

    result = storage.list_prefix("templates/")
    assert sorted(result) == ["templates/t1.json", "templates/t2.json"]


def test_local_storage_path_traversal_blocked(tmp_path):
    storage = LocalFilesystemStorage(str(tmp_path))
    with pytest.raises(ValueError, match="Path traversal"):
        storage.read_bytes("../etc/passwd")
    with pytest.raises(ValueError, match="Path traversal"):
        storage.write_bytes("../escape.txt", b"x")


def test_factory_local_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_MODE", "local")
    monkeypatch.setenv("STORAGE_PATH", str(tmp_path))
    storage = create_storage()
    assert isinstance(storage, LocalFilesystemStorage)


def test_factory_explicit_overrides_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_MODE", "gcs")
    monkeypatch.setenv("GCS_BUCKET", "should-be-ignored")
    storage = create_storage(mode="local", path=str(tmp_path))
    assert isinstance(storage, LocalFilesystemStorage)


def test_factory_unknown_mode_raises(monkeypatch):
    monkeypatch.delenv("STORAGE_MODE", raising=False)
    with pytest.raises(ValueError, match="Unknown STORAGE_MODE"):
        create_storage(mode="nonsense")  # type: ignore[arg-type]


def test_protocol_compliance(tmp_path):
    from signfinder.storage import StorageBackend
    storage = LocalFilesystemStorage(str(tmp_path))
    assert isinstance(storage, StorageBackend)


# ── REGRESSION: несуществующие методы должны давать AttributeError ───────────

def test_read_method_does_not_exist(tmp_path):
    """sf.storage.read() не существует → AttributeError (не тихий None)."""
    storage = LocalFilesystemStorage(str(tmp_path))
    assert not hasattr(storage, "read"), "read() не должен существовать в StorageBackend"


def test_write_method_does_not_exist(tmp_path):
    """sf.storage.write() не существует → AttributeError."""
    storage = LocalFilesystemStorage(str(tmp_path))
    assert not hasattr(storage, "write"), "write() не должен существовать в StorageBackend"


def test_get_method_does_not_exist(tmp_path):
    """sf.storage.get() не существует."""
    storage = LocalFilesystemStorage(str(tmp_path))
    assert not hasattr(storage, "get")


def test_put_method_does_not_exist(tmp_path):
    """sf.storage.put() не существует."""
    storage = LocalFilesystemStorage(str(tmp_path))
    assert not hasattr(storage, "put")


def test_correct_methods_exist(tmp_path):
    """Правильные методы: read_bytes, write_bytes, read_json, write_json, exists, delete, list_prefix."""
    storage = LocalFilesystemStorage(str(tmp_path))
    for method in ("read_bytes", "write_bytes", "read_json", "write_json",
                   "read_text", "write_text", "exists", "delete", "list_prefix"):
        assert hasattr(storage, method), f"Метод {method} отсутствует"
