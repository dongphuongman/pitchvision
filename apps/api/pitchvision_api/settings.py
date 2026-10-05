"""Runtime settings from PV_* environment variables."""
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Env = Literal["local", "test", "staging", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PV_", extra="ignore")

    env: Env = "local"
    # the API connects as pv_app (non-owner) so Postgres RLS applies; migrations use PV_MIGRATE_DATABASE_URL
    database_url: str = "postgresql+asyncpg://pv_app:pv_app@localhost:5432/pitchvision"
    redis_url: str = "redis://localhost:6379/0"
    storage_endpoint: str | None = None  # None = AWS default; R2 / SeaweedFS / MinIO set an endpoint URL
    storage_bucket: str = "pv-local-data"
    cors_origins: Annotated[list[str], NoDecode] = ["http://localhost:5173"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        """Accept `a,b` as well as a list (env vars are plain strings)."""
        return [o.strip() for o in v.split(",") if o.strip()] if isinstance(v, str) else v
