"""Intake adapters — Protocol definitions.

Интерфейс не привязан к IMAP: IMAP — первый адаптер.
Graph API / Gmail API / watched folder — встанут позже.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class IntakeAttachment:
    filename: str
    content: bytes
    content_type: str


@dataclass
class IntakeMessage:
    uid: str
    subject: str
    sender: str
    received_at: str
    attachments: list[IntakeAttachment] = field(default_factory=list)


@runtime_checkable
class IntakeSource(Protocol):
    def poll(self) -> list[IntakeMessage]: ...
    def move(self, uid: str, dest_folder: str, source_folder: str | None = None) -> None: ...
    def append(self, folder: str, raw_email: bytes) -> None: ...
    def fetch_raw(self, uid: str) -> bytes: ...


@runtime_checkable
class IntakeSink(Protocol):
    def deliver(
        self,
        to_addr: str,
        subject: str,
        body: str,
        attachments: list[IntakeAttachment],
    ) -> None: ...
