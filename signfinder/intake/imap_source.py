"""IMAP intake source — imaplib (stdlib), без внешних зависимостей.

Поддержка:
  - SSL / non-SSL
  - UID SEARCH UNSEEN → fetch → mark \\Seen
  - move: UID MOVE (RFC 6851) с fallback на COPY+DELETE+EXPUNGE
  - append: IMAP APPEND для укладки обработанного письма
  - fetch_raw: UID FETCH RFC822
"""
from __future__ import annotations

import email as _email
import email.header as _eh
import imaplib
import logging
import time
from typing import Optional

from signfinder.intake.base import IntakeAttachment, IntakeMessage

logger = logging.getLogger(__name__)

# Content-types для PDF и DOCX
_PDF_CTYPES = {"application/pdf", "application/x-pdf"}
_DOCX_CTYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/msword",
    "application/vnd.ms-word",
}


def _decode_header(value: str | None) -> str:
    """Декодирует RFC 2047 encoded header."""
    if not value:
        return ""
    parts = []
    for bstr, charset in _eh.decode_header(value):
        if isinstance(bstr, bytes):
            try:
                parts.append(bstr.decode(charset or "utf-8", errors="replace"))
            except Exception:
                parts.append(bstr.decode("latin-1", errors="replace"))
        else:
            parts.append(bstr)
    return "".join(parts)


class ImapSource:
    """IMAP-адаптер для SignFinder Intake.

    Одно подключение на экземпляр, переподключение при потере сессии.
    Потоконебезопасен — использовать из одного треда (поллер).
    """

    def __init__(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        ssl: bool,
        folder_in: str,
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
        self._ssl = ssl
        self._folder_in = folder_in
        self._imap: imaplib.IMAP4 | None = None
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

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self) -> None:
        if self._imap is not None:
            try:
                self._imap.noop()
                return  # ещё живой
            except Exception:
                self._imap = None

        logger.info("IMAP connect %s:%d ssl=%s", self._host, self._port, self._ssl)
        if self._ssl:
            self._imap = imaplib.IMAP4_SSL(self._host, self._port)
        else:
            self._imap = imaplib.IMAP4(self._host, self._port)

        if self._auth_method == "xoauth2" and self._oauth is not None:
            from signfinder.intake.oauth2 import build_xoauth2_string
            token = self._oauth.get_access_token()
            auth_bytes = build_xoauth2_string(self._user, token)
            typ, _ = self._imap.authenticate("XOAUTH2", lambda _: auth_bytes)
            if typ != "OK":
                raise RuntimeError(f"IMAP XOAUTH2 auth failed: {typ}")
            logger.info("IMAP XOAUTH2 logged in as %s (%s)", self._user, self._oauth._provider)
        else:
            self._imap.login(self._user, self._password)
            logger.info("IMAP logged in as %s (basic)", self._user)

    def close(self) -> None:
        if self._imap:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    # ── IntakeSource protocol ─────────────────────────────────────────────────

    def poll(self) -> list[IntakeMessage]:
        """Возвращает непрочитанные письма из folder_in. Метит \\Seen."""
        self._connect()
        assert self._imap is not None

        folder = self._quote(self._folder_in)
        typ, _ = self._imap.select(folder)
        if typ != "OK":
            logger.error("IMAP SELECT %s failed: %s", folder, typ)
            return []

        typ, data = self._imap.uid("search", None, "UNSEEN")
        if typ != "OK" or not data or not data[0]:
            return []

        uid_list = data[0].split()
        if not uid_list:
            return []

        logger.info("Found %d UNSEEN messages in %s", len(uid_list), self._folder_in)
        messages: list[IntakeMessage] = []

        for uid_bytes in uid_list:
            uid = uid_bytes.decode()
            try:
                msg = self._fetch_and_parse(uid)
                if msg is not None:
                    messages.append(msg)
            except Exception as e:
                logger.error("Failed to fetch uid=%s: %s", uid, e)

        # Закрыть соединение после чтения — FETCH больших писем оставляет
        # рассинхрон в буфере, который ломает последующие SELECT (Gmail).
        # append/move получат свежее соединение через _connect().
        self.close()

        return messages

    def move(
        self,
        uid: str,
        dest_folder: str,
        source_folder: str | None = None,
    ) -> None:
        """Перемещает письмо uid из source_folder в dest_folder."""
        self._connect()
        assert self._imap is not None

        src = source_folder or self._folder_in

        # Гарантировать что папка-назначение существует
        self._ensure_folder(dest_folder)

        # SELECT source — ОБЯЗАТЕЛЬНО проверять результат, иначе COPY/MOVE падает в AUTH
        typ, _ = self._imap.select(self._quote(src))
        if typ != "OK":
            raise RuntimeError(f"IMAP SELECT {src} failed: {typ}")

        # Попытка UID MOVE (RFC 6851, Gmail поддерживает)
        try:
            typ, _ = self._imap.uid("move", uid, self._quote(dest_folder))
            if typ == "OK":
                logger.debug("UID MOVE %s → %s OK", uid, dest_folder)
                return
        except Exception as e:
            logger.debug("UID MOVE failed (%s), fallback COPY+DELETE", e)
            # После исключения соединение могло сброситься в AUTH —
            # переподключиться и заново SELECT перед COPY
            self._imap = None
            self._connect()
            typ, _ = self._imap.select(self._quote(src))
            if typ != "OK":
                raise RuntimeError(f"IMAP re-SELECT {src} failed: {typ}")

        # Fallback: COPY + DELETE + EXPUNGE
        typ, _ = self._imap.uid("copy", uid, self._quote(dest_folder))
        if typ != "OK":
            raise RuntimeError(f"IMAP COPY {uid} → {dest_folder} failed: {typ}")
        self._imap.uid("store", uid, "+FLAGS", "\\Deleted")
        self._imap.expunge()
        logger.debug("COPY+DELETE %s → %s done", uid, dest_folder)

    def append(self, folder: str, raw_email: bytes) -> None:
        """Кладёт raw_email в IMAP-папку folder."""
        self._connect()
        assert self._imap is not None

        self._ensure_folder(folder)   # создать папку если нет

        date_time = imaplib.Time2Internaldate(time.time())
        typ, data = self._imap.append(
            self._quote(folder),
            "\\Seen",
            date_time,
            raw_email,
        )
        if typ != "OK":
            raise RuntimeError(f"IMAP APPEND to {folder} failed: {typ} {data}")
        logger.debug("APPEND to %s OK (%d bytes)", folder, len(raw_email))

    def fetch_raw(self, uid: str, source_folder: str | None = None) -> bytes:
        """Возвращает сырые байты письма по UID."""
        self._connect()
        assert self._imap is not None

        src = source_folder or self._folder_in
        typ, _ = self._imap.select(self._quote(src))
        if typ != "OK":
            raise RuntimeError(f"IMAP SELECT {src} failed in fetch_raw: {typ}")
        typ, data = self._imap.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            raise RuntimeError(f"FETCH {uid} failed: {typ}")
        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        return raw

    # ── Internal ──────────────────────────────────────────────────────────────

    def _ensure_folder(self, folder: str) -> None:
        """Создаёт IMAP-папку/ярлык если не существует. Идемпотентно.
        БЕЗ SELECT — SELECT на Gmail даёт многословный ответ и ломает парсер imaplib.
        """
        assert self._imap is not None
        quoted = self._quote(folder)
        try:
            typ, data = self._imap.create(quoted)
            if typ == "OK":
                logger.info("IMAP CREATE %s OK", folder)
            else:
                # NO = папка/ярлык уже существует — это норма, не ошибка
                logger.debug("IMAP CREATE %s: %s (вероятно уже есть)", folder, typ)
        except Exception as e:
            # CREATE существующей папки может кинуть — не критично
            logger.debug("IMAP CREATE %s exception: %s (вероятно уже есть)", folder, e)

    def _fetch_and_parse(self, uid: str) -> Optional[IntakeMessage]:
        assert self._imap is not None

        # Метим как прочитанное сразу, чтобы повторный poll не взял снова
        self._imap.uid("store", uid, "+FLAGS", "\\Seen")

        typ, data = self._imap.uid("fetch", uid, "(RFC822)")
        if typ != "OK" or not data or not data[0]:
            return None

        raw = data[0][1] if isinstance(data[0], tuple) else data[0]
        if not isinstance(raw, bytes):
            return None

        msg = _email.message_from_bytes(raw)
        subject = _decode_header(msg.get("Subject", ""))
        sender = _decode_header(msg.get("From", ""))
        date_str = msg.get("Date", "")

        attachments: list[IntakeAttachment] = []
        for part in msg.walk():
            ctype = (part.get_content_type() or "").lower()
            fname = part.get_filename()
            if fname:
                fname = _decode_header(fname)

            is_pdf = ctype in _PDF_CTYPES or bool(fname and fname.lower().endswith(".pdf"))
            is_docx = ctype in _DOCX_CTYPES or bool(fname and fname.lower().endswith(".docx"))

            if not (is_pdf or is_docx):
                continue

            payload = part.get_payload(decode=True)
            if not payload:
                continue

            ext = "pdf" if is_pdf else "docx"
            attachments.append(IntakeAttachment(
                filename=fname or f"attachment_{len(attachments)}.{ext}",
                content=payload,
                content_type=ctype,
            ))

        logger.info(
            "uid=%s subject=%r sender=%s attachments=%d",
            uid, subject, sender, len(attachments),
        )
        return IntakeMessage(
            uid=uid,
            subject=subject,
            sender=sender,
            received_at=date_str,
            attachments=attachments,
        )

    @staticmethod
    def _quote(folder: str) -> str:
        """Добавляет кавычки если папка содержит пробелы."""
        if " " in folder:
            return f'"{folder}"'
        return folder
