"""signfinder.intake — канал неинтерактивного приёма документов.

IMAP — первый адаптер. Graph API / Gmail API / watched folder встанут позже.
"""
from signfinder.intake.base import IntakeAttachment, IntakeMessage, IntakeSink, IntakeSource
from signfinder.intake.imap_source import ImapSource
from signfinder.intake.smtp_sink import SmtpSink, build_processed_email

__all__ = [
    "IntakeAttachment",
    "IntakeMessage",
    "IntakeSource",
    "IntakeSink",
    "ImapSource",
    "SmtpSink",
    "build_processed_email",
]
