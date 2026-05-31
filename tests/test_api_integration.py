"""API integration tests (v1.15).

Вызовы к http://api:8000 с минимальным PDF.
Запускаются только если API доступен (SIGNFINDER_API_URL).
Skip при недоступности — не блокируют CI на unit-тестах.
"""
from __future__ import annotations

import io
import os

import fitz
import pytest
import httpx


# ── Skip если API недоступен ─────────────────────────────────────────────────

API_URL = os.environ.get("SIGNFINDER_API_URL", "http://api:8000")
API_KEY = os.environ.get("SIGNFINDER_API_KEY", "")


def _api_available() -> bool:
    try:
        r = httpx.get(f"{API_URL}/healthz", timeout=3.0)
        return r.status_code < 500
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _api_available(),
    reason=f"API недоступен: {API_URL}",
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {"Authorization": f"Bearer {API_KEY}"}


def _min_pdf_bytes() -> bytes:
    """Минимальный валидный PDF для тестов."""
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((50, 50), "LEASE AGREEMENT test for integration tests", fontsize=10)
    p.insert_text((50, 80), "Lessor: ___________________", fontsize=10)
    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def _broken_pdf_bytes() -> bytes:
    return b"NOT A PDF CONTENT XYZ"


def _min_png_bytes() -> bytes:
    from PIL import Image
    img = Image.new("RGBA", (100, 30), (0, 0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── /v1/analyze ──────────────────────────────────────────────────────────────

def test_analyze_returns_200_with_traffic_light():
    """POST /v1/analyze → 200, поле traffic_light в ответе."""
    pdf = _min_pdf_bytes()
    with httpx.Client(timeout=60.0) as c:
        r = c.post(
            f"{API_URL}/v1/analyze",
            headers=_headers(),
            files={"file": ("test.pdf", pdf, "application/pdf")},
        )
    assert r.status_code == 200
    data = r.json()
    assert "traffic_light" in data
    assert data["traffic_light"] in ("green", "yellow", "no_match")


def test_analyze_broken_pdf_returns_no_match():
    """POST /v1/analyze с битым PDF → 200, traffic_light='no_match'."""
    with httpx.Client(timeout=30.0) as c:
        r = c.post(
            f"{API_URL}/v1/analyze",
            headers=_headers(),
            files={"file": ("broken.pdf", _broken_pdf_bytes(), "application/pdf")},
        )
    assert r.status_code == 200
    data = r.json()
    assert data.get("traffic_light") == "no_match"


# ── /v1/analyze/batch ────────────────────────────────────────────────────────

def test_analyze_batch_two_files():
    """POST /v1/analyze/batch с 2 файлами → total=2."""
    pdf = _min_pdf_bytes()
    with httpx.Client(timeout=120.0) as c:
        r = c.post(
            f"{API_URL}/v1/analyze/batch",
            headers=_headers(),
            files=[
                ("files", ("doc1.pdf", pdf, "application/pdf")),
                ("files", ("doc2.pdf", pdf, "application/pdf")),
            ],
        )
    assert r.status_code == 200
    data = r.json()
    assert data.get("total") == 2
    assert "items" in data


# ── /v1/sign (без подписи) ────────────────────────────────────────────────────

def test_sign_without_signature_returns_error():
    """POST /v1/sign без загруженной подписи в storage → 404 или ошибка."""
    pdf = _min_pdf_bytes()
    import json
    anchors = []
    with httpx.Client(timeout=30.0) as c:
        r = c.post(
            f"{API_URL}/v1/sign",
            headers=_headers(),
            files={"file": ("test.pdf", pdf, "application/pdf")},
            data={"anchors_json": json.dumps(anchors), "signer_id": "default"},
        )
    # Без подписи — 404 или возможно подпись уже есть (200). Главное — не 500.
    assert r.status_code in (200, 404, 422)


# ── /v1/signers/default ──────────────────────────────────────────────────────

def test_get_signer_default():
    """GET /v1/signers/default → 200."""
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{API_URL}/v1/signers/default", headers=_headers())
    assert r.status_code == 200


def test_put_signer_signature():
    """PUT /v1/signers/default/signature + PNG → 204."""
    png = _min_png_bytes()
    with httpx.Client(timeout=10.0) as c:
        r = c.put(
            f"{API_URL}/v1/signers/default/signature",
            headers=_headers(),
            files={"file": ("signature.png", png, "image/png")},
        )
    assert r.status_code in (200, 204)


# ── /v1/config/llm ───────────────────────────────────────────────────────────

def test_get_llm_config():
    """GET /v1/config/llm → 200, есть поле active_provider."""
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{API_URL}/v1/config/llm", headers=_headers())
    assert r.status_code == 200
    data = r.json()
    assert "active_provider" in data


# ── /v1/settings/sign-mode ───────────────────────────────────────────────────

def test_get_sign_mode():
    """GET /v1/settings/sign-mode → 200, есть use_signature и use_marker."""
    with httpx.Client(timeout=10.0) as c:
        r = c.get(f"{API_URL}/v1/settings/sign-mode", headers=_headers())
    assert r.status_code == 200
    data = r.json()
    assert "use_signature" in data
    assert "use_marker" in data
