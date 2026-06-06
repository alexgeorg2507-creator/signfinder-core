"""OAuth2 (XOAUTH2) для IMAP/SMTP. Получение access_token по refresh_token.

Провайдеро-независимый: endpoint/scope задаются в конфиге.
Все провайдеры используют одинаковый OAuth2 refresh-flow, разные URL.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TOKEN_ENDPOINTS = {
    "google":    "https://oauth2.googleapis.com/token",
    "microsoft": "https://login.microsoftonline.com/common/oauth2/v2.0/token",
    "yandex":    "https://oauth.yandex.ru/token",
    "mailru":    "https://oauth.mail.ru/token",
    "rambler":   "https://id.rambler.ru/oauth/token",
}


class OAuth2TokenProvider:
    """Хранит refresh_token, выдаёт свежий access_token (кэширует до истечения)."""

    def __init__(
        self,
        provider: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        token_endpoint: str = "",
    ) -> None:
        self._provider = provider
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._endpoint = token_endpoint or TOKEN_ENDPOINTS.get(provider, "")
        if not self._endpoint:
            raise ValueError(f"Unknown OAuth2 provider '{provider}', specify token_endpoint")
        self._access_token: str = ""
        self._expires_at: float = 0.0

    def get_access_token(self) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        self._refresh()
        return self._access_token

    def _refresh(self) -> None:
        data = urllib.parse.urlencode({
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "refresh_token": self._refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(self._endpoint, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except Exception as e:
            raise RuntimeError(f"OAuth2 token refresh failed ({self._provider}): {e}")
        self._access_token = payload["access_token"]
        self._expires_at = time.time() + int(payload.get("expires_in", 3600))
        logger.info("OAuth2 token refreshed for %s (expires in %ss)",
                    self._provider, payload.get("expires_in"))


def build_xoauth2_string(user: str, access_token: str) -> bytes:
    """Формирует SASL XOAUTH2 строку для imaplib.authenticate / SMTP."""
    raw = f"user={user}\x01auth=Bearer {access_token}\x01\x01"
    return raw.encode()
