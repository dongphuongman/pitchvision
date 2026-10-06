"""EmailSender port + adapters (console | smtp | resend) and the transactional templates."""

from pitchvision_identity.mail.senders import (
    ConsoleEmailSender,
    EmailMessage,
    EmailSender,
    ResendEmailSender,
    SmtpEmailSender,
    make_sender,
    send_quietly,
)
from pitchvision_identity.mail.templates import invite_email, reset_email, upgrade_request_email, verify_email

__all__ = [
    "ConsoleEmailSender",
    "EmailMessage",
    "EmailSender",
    "ResendEmailSender",
    "SmtpEmailSender",
    "invite_email",
    "make_sender",
    "reset_email",
    "send_quietly",
    "upgrade_request_email",
    "verify_email",
]
