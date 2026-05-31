"""Общие фикстуры для тестов signfinder-core v1.15."""
from __future__ import annotations

import io
import os

import fitz
import pytest

from signfinder.storage import LocalFilesystemStorage


@pytest.fixture
def pdf_bytes() -> bytes:
    """Минимальный валидный 2-страничный PDF (Latin, без кириллицы — дефолтный шрифт)."""
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((50, 50), "LEASE AGREEMENT No 001/2026", fontsize=12)
    p1.insert_text(
        (50, 80),
        "Romashka LLC, hereinafter referred to as the Lessor, "
        "represented by Director Ivanov I.I., on one hand, "
        "and Lutik LLC, hereinafter referred to as the Lessee, "
        "represented by Director Petrov P.P., on the other hand.",
        fontsize=10,
    )
    p2 = doc.new_page()
    p2.insert_text((50, 50), "Signatures:", fontsize=10)
    p2.insert_text((50, 80), "Lessor: ___________________  /Ivanov I.I./", fontsize=10)
    p2.insert_text((50, 110), "Lessee: ___________________  /Petrov P.P./", fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


@pytest.fixture
def local_storage(tmp_path):
    """LocalFilesystemStorage в tmp директории."""
    return LocalFilesystemStorage(str(tmp_path))


@pytest.fixture
def api_base_url() -> str:
    return os.environ.get("SIGNFINDER_API_URL", "http://api:8000")


@pytest.fixture
def api_headers() -> dict:
    key = os.environ.get("SIGNFINDER_API_KEY", "test-key")
    return {"Authorization": f"Bearer {key}"}
