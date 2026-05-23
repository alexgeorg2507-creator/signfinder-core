"""Тесты storage abstraction."""
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
    # Повторное удаление — False
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
    # mode="local" перекрывает env
    storage = create_storage(mode="local", path=str(tmp_path))
    assert isinstance(storage, LocalFilesystemStorage)


def test_factory_unknown_mode_raises(monkeypatch):
    monkeypatch.delenv("STORAGE_MODE", raising=False)
    with pytest.raises(ValueError, match="Unknown STORAGE_MODE"):
        create_storage(mode="nonsense")  # type: ignore[arg-type]


def test_protocol_compliance(tmp_path):
    """LocalFilesystemStorage соответствует StorageBackend Protocol."""
    from signfinder.storage import StorageBackend
    storage = LocalFilesystemStorage(str(tmp_path))
    assert isinstance(storage, StorageBackend)
