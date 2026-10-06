"""Runtime settings from PV_* environment variables."""
from typing import Annotated, Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Env = Literal["local", "test", "staging", "production"]
Region = Literal["eu", "us"]

GIB = 1024**3


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PV_", extra="ignore")

    env: Env = "local"
    # the API connects as pv_app (non-owner) so Postgres RLS applies; migrations use PV_MIGRATE_DATABASE_URL
    database_url: str = "postgresql+asyncpg://pv_app:pv_app@localhost:5432/pitchvision"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    # --- auth: access tokens are minted by identity-svc (docs/contracts/identity-api.md) ---
    region: Region = "eu"  # this deployment's data region; tokens for another region get 409 REGION_MISMATCH
    jwks_url: str = "http://localhost:8001/.well-known/jwks.json"
    jwt_issuer: str = "http://localhost:8001"
    jwt_audience: str = "pitchvision"
    jwks_timeout_s: float = 3.0
    jwks_cache_ttl_s: float = 600.0
    # LOCAL-ONLY escape hatch: trust X-Dev-Org/X-Dev-User/X-Dev-Role headers. Refused outside PV_ENV=local.
    dev_auth: bool = False

    # --- object storage (S3 API: R2 in cloud, SeaweedFS locally) ---
    storage_endpoint: str | None = None  # server-side calls; None = AWS default
    storage_public_endpoint: str | None = None  # host in presigned URLs the browser uses; None = storage_endpoint
    storage_bucket: str = "pv-local-data"
    storage_region: str = "auto"  # R2 wants "auto"
    storage_access_key: str | None = None  # None = boto3 default credential chain
    storage_secret_key: str | None = None
    presign_ttl_s: int = 900  # AD §14: presigned URLs live 15 min
    max_upload_bytes: int = 10 * GIB

    # --- jobs (M1-BE-09): every job pins the image it runs on; workers refuse tasks for another tag (AD §7) ---
    image_tag: str = "dev"

    # --- WebSocket (M1-BE-12, AD §10.3) ---
    ws_heartbeat_s: float = 25.0
    ws_max_topics: int = 50  # subscriptions per connection
    ws_subscribe_burst: int = 20  # subscribe ops allowed per window
    ws_subscribe_window_s: float = 10.0

    # --- YouTube ingest (M1-BE-08) ---
    probe_timeout_s: float = 20.0
    max_video_duration_s: int = 3 * 3600

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept `a,b` as well as a list (env vars are plain strings)."""
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v

    @model_validator(mode="after")
    def _dev_auth_only_local(self) -> "Settings":
        if self.dev_auth and self.env != "local":
            raise ValueError("PV_DEV_AUTH=1 is only allowed with PV_ENV=local")
        return self
