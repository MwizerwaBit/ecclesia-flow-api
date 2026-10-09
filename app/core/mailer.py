"""Outgoing email.

Two senders:

- ``SmtpMailer`` when ``SMTP_HOST`` is set — any SMTP relay (SES, Postmark,
  Mailgun, a Google Workspace relay, ...).
- ``OutboxMailer`` otherwise — keeps messages in memory (tests read
  ``outbox``) and, in development only, writes each one to ``DEV_OUTBOX_DIR``
  so a developer can open the link. Production refuses to start without
  SMTP (see ``app.main``).

Message bodies can contain one-time links (password resets). They are never
logged — only the recipient's domain and the subject are.
"""

import asyncio
import logging
import smtplib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from app.core.config import get_settings

logger = logging.getLogger("ecclesia_flow.mail")


@dataclass(frozen=True)
class OutgoingEmail:
    to: str
    subject: str
    body: str


def _redacted_to(address: str) -> str:
    return "***@" + address.rsplit("@", 1)[-1]


def _write_dev_copy(folder: Path, email: OutgoingEmail) -> None:
    folder.mkdir(exist_ok=True)
    name = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}.txt"
    (folder / name).write_text(f"To: {email.to}\nSubject: {email.subject}\n\n{email.body}\n", encoding="utf-8")


class OutboxMailer:
    def __init__(self) -> None:
        self.outbox: list[OutgoingEmail] = []

    async def send(self, email: OutgoingEmail) -> None:
        self.outbox.append(email)
        del self.outbox[:-100]
        settings = get_settings()
        if settings.environment == "development":
            await asyncio.to_thread(_write_dev_copy, Path(settings.dev_outbox_dir), email)
        logger.info("mail.queued to=%s subject=%s", _redacted_to(email.to), email.subject)


class SmtpMailer:
    async def send(self, email: OutgoingEmail) -> None:
        await asyncio.to_thread(self._send, email)
        logger.info("mail.sent to=%s subject=%s", _redacted_to(email.to), email.subject)

    def _send(self, email: OutgoingEmail) -> None:
        settings = get_settings()
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = email.to
        msg["Subject"] = email.subject
        msg.set_content(email.body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_username:
                smtp.login(settings.smtp_username, settings.smtp_password)
            smtp.send_message(msg)


_mailer: OutboxMailer | SmtpMailer | None = None


def get_mailer() -> OutboxMailer | SmtpMailer:
    global _mailer
    if _mailer is None:
        _mailer = SmtpMailer() if get_settings().smtp_host else OutboxMailer()
    return _mailer


async def send_quietly(email: OutgoingEmail) -> None:
    """For background sends after the response: a delivery failure is logged,
    never surfaced to the requester (who must not learn anything from it)."""
    try:
        await get_mailer().send(email)
    except Exception:  # noqa: BLE001 — any provider failure is handled the same way
        logger.exception("mail.failed to=%s subject=%s", _redacted_to(email.to), email.subject)
