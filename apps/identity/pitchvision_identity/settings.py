"""Runtime settings from PV_* environment variables (docs/contracts/identity-api.md)."""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Env = Literal["local", "test", "staging", "production"]
EmailBackend = Literal["console", "smtp", "resend"]

DEV_ENVS: frozenset[str] = frozenset({"local", "test"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PV_", extra="ignore")

    env: Env = "local"
    identity_database_url: str = "postgresql+asyncpg://pitchvision:pitchvision@localhost:5432/pitchvision_identity"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # --- JWT (RS256). Key from PEM text or a file; an ephemeral key only in local/test.
    jwt_issuer: str = "http://localhost:3000"
    jwt_audience: str = "pitchvision"
    jwt_private_key_pem: str | None = None
    jwt_private_key_file: Path | None = None
    jwt_kid: str | None = None  # default: RFC 7638 thumbprint of the public key
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600

    # --- links + cookie
    web_url: str = "http://localhost:3000"  # email links and the OIDC callback redirect point here
    public_url: str | None = None  # where browsers reach identity (OIDC redirect_uri); default = web_url
    cookie_secure: bool = True

    # --- email
    email_backend: EmailBackend = "console"
    email_from: str = "PitchVision <no-reply@pitchvision.local>"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    smtp_ssl: bool = False
    resend_api_key: str | None = None
    # service-to-service (workers → POST /internal/v1/notifications/email); unset = internal routes answer 404
    internal_token: str | None = None

    # --- Google OIDC (disabled unless both id and secret are set)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_discovery_url: str = "https://accounts.google.com/.well-known/openid-configuration"

    # --- argon2id cost (argon2-cffi defaults = RFC 9106 low-memory profile); tests lower them
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 64 * 1024
    argon2_parallelism: int = 4

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept `a,b` as well as a list (env vars are plain strings)."""
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v

    @field_validator("web_url", "public_url", "jwt_issuer")
    @classmethod
    def _strip_slash(cls, v: str | None) -> str | None:
        return v.rstrip("/") if v else v

    @model_validator(mode="after")
    def _guards(self) -> "Settings":
        if self.env not in DEV_ENVS:
            if not (self.jwt_private_key_pem or self.jwt_private_key_file):
                raise ValueError("PV_JWT_PRIVATE_KEY_PEM or PV_JWT_PRIVATE_KEY_FILE is required outside local/test")
            if not self.cookie_secure:
                raise ValueError("PV_COOKIE_SECURE must be true outside local/test")
        if self.email_backend == "resend" and not self.resend_api_key:
            raise ValueError("PV_EMAIL_BACKEND=resend needs PV_RESEND_API_KEY")
        return self

    @property
    def is_dev(self) -> bool:
        return self.env in DEV_ENVS

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def identity_public_url(self) -> str:
        return self.public_url or self.web_url
