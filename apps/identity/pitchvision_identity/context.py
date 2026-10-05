"""Process-wide collaborators, built once by create_app and stored on app.state.ctx."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from pitchvision_identity.mail import EmailSender
from pitchvision_identity.ratelimit import RateLimiter
from pitchvision_identity.security import KeyRing, PasswordHasher
from pitchvision_identity.settings import Settings

if TYPE_CHECKING:
    from pitchvision_identity.services.oidc import GoogleOidc


@dataclass
class Ctx:
    settings: Settings
    sm: async_sessionmaker[AsyncSession]
    keys: KeyRing
    hasher: PasswordHasher
    mailer: EmailSender
    limiter: RateLimiter
    http: httpx.AsyncClient  # outbound calls: Google OIDC, Resend
    google: "GoogleOidc | None" = None  # None = Google sign-in not configured
