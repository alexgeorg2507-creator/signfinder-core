"""SMTP sink — отправка ответного письма отправителю.

Опциональный адаптер. Если SMTP_HOST не задан — не используется.
"""
from __future__ import annotations

import logging
import smtplib
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

from signfinder.intake.base import IntakeAttachment

logger = logging.getLogger(__name__)


class SmtpSink:
    """SMTP-адаптер для отправки ответных писем."""

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str | None = None,
        auth_method: str = "basic",
        oauth2_provider: str = "",
        oauth2_client_id: str = "",
        oauth2_client_secret: str = "",
        oauth2_refresh_token: str = "",
        oauth2_token_endpoint: str = "",
    ) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from = from_addr or user
        self._auth_method = auth_method
        self._oauth = None
        if auth_method == "xoauth2":
            from signfinder.intake.oauth2 import OAuth2TokenProvider
            self._oauth = OAuth2TokenProvider(
                provider=oauth2_provider,
                client_id=oauth2_client_id,
                client_secret=oauth2_client_secret,
                refresh_token=oauth2_refresh_token,
                token_endpoint=oauth2_token_endpoint,
            )

    def deliver(
        self,
        to_addr: str,
        subject: str,
        body: str,
        attachments: list[IntakeAttachment],
    ) -> None:
        """Отправляет письмо с вложениями."""
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = to_addr
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain="signfinder.local")

        msg.attach(MIMEText(body, "plain", "utf-8"))

        for att in attachments:
            part = MIMEApplication(att.content, Name=att.filename)
            part["Content-Disposition"] = f'attachment; filename="{att.filename}"'
            msg.attach(part)

        try:
            with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
                smtp.starttls()
                if self._auth_method == "xoauth2" and self._oauth is not None:
                    import base64
                    from signfinder.intake.oauth2 import build_xoauth2_string
                    token = self._oauth.get_access_token()
                    auth_bytes = build_xoauth2_string(self._user, token)
                    smtp.auth("XOAUTH2", lambda challenge=None: auth_bytes)
                    logger.info("SMTP XOAUTH2 logged in as %s (%s)", self._user, self._oauth._provider)
                elif self._user and self._password:
                    smtp.login(self._user, self._password)
                smtp.send_message(msg)
            logger.info("SMTP delivered to %s subject=%r", to_addr, subject)
        except Exception as e:
            logger.error("SMTP deliver failed to %s: %s", to_addr, e)
            raise


def build_processed_email(
    original_subject: str,
    original_sender: str,
    body_text: str,
    signed_pdfs: list[tuple[str, bytes]],
) -> bytes:
    """Строит RFC 2822 байты для IMAP APPEND."""
    msg = MIMEMultipart()
    msg["Subject"] = f"[SignFinder] {original_subject}"
    msg["From"] = "SignFinder Agent <noreply@signfinder.local>"
    msg["To"] = original_sender
    msg["Date"] = formatdate(localtime=False)
    msg["Message-ID"] = make_msgid(domain="signfinder.local")

    msg.attach(MIMEText(body_text, "plain", "utf-8"))

    for fname, pdf_bytes in signed_pdfs:
        part = MIMEApplication(pdf_bytes, Name=fname)
        part["Content-Disposition"] = f'attachment; filename="{fname}"'
        msg.attach(part)

    return msg.as_bytes()
