"""Email delivery adapters. `console` logs the message (local default), `smtp` (Mailpit in compose), `resend` (prod)."""

import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from typing import Protocol

import httpx

from pitchvision_identity.settings import Settings

log = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str
    link: str  # the one action link; console logs it, tests read it


class EmailSender(Protocol):
    async def send(self, msg: EmailMessage) -> None: ...


class ConsoleEmailSender:
    async def send(self, msg: EmailMessage) -> None:
        log.warning("email (console backend) to=%s subject=%r link=%s", msg.to, msg.subject, msg.link)


class SmtpEmailSender:
    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _send_sync(self, msg: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"], mime["To"], mime["Subject"] = self._s.email_from, msg.to, msg.subject
        mime.set_content(msg.text)
        mime.add_alternative(msg.html, subtype="html")
        s = self._s
        smtp: smtplib.SMTP = (
            smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=10, context=ssl.create_default_context())
            if s.smtp_ssl
            else smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=10)
        )
        with smtp:
            if s.smtp_starttls and not s.smtp_ssl:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(mime)

    async def send(self, msg: EmailMessage) -> None:
        await asyncio.to_thread(self._send_sync, msg)


class ResendEmailSender:
    def __init__(self, settings: Settings, http: httpx.AsyncClient) -> None:
        self._s, self._http = settings, http

    async def send(self, msg: EmailMessage) -> None:
        r = await self._http.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {self._s.resend_api_key}"},
            json={
                "from": self._s.email_from,
                "to": [msg.to],
                "subject": msg.subject,
                "text": msg.text,
                "html": msg.html,
            },
            timeout=10,
        )
        r.raise_for_status()


async def send_quietly(sender: EmailSender, msg: EmailMessage) -> None:
    """Delivery never fails the request (the user can resend); failures are logged."""
    try:
        await sender.send(msg)
    except Exception:
        log.exception("email delivery failed to=%s subject=%r", msg.to, msg.subject)


def make_sender(settings: Settings, http: httpx.AsyncClient) -> EmailSender:
    match settings.email_backend:
        case "smtp":
            return SmtpEmailSender(settings)
        case "resend":
            return ResendEmailSender(settings, http)
        case _:
            return ConsoleEmailSender()
