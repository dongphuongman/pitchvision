"""SQLAlchemy 2.0 models mirroring migration 0001 (AD §9.4). The migration is the source of truth for DDL."""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, MetaData, Text, func, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

TZ = TIMESTAMP(timezone=True)
GEN_UUID = text("gen_random_uuid()")


class Base(DeclarativeBase):
    metadata = MetaData()


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=GEN_UUID)


def _created() -> Mapped[datetime]:
    return mapped_column(TZ, nullable=False, server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)  # normalised: trimmed + lowercase
    password_hash: Mapped[str | None] = mapped_column(Text)  # NULL = Google-only account
    email_verified_at: Mapped[datetime | None] = mapped_column(TZ)
    signup_plan: Mapped[str | None] = mapped_column(Text)
    last_active_org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


class Org(Base):
    __tablename__ = "orgs"

    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str] = mapped_column(Text, nullable=False)  # eu|us, immutable (trigger)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (Index("memberships_user_id_idx", "user_id"),)

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)  # "my role" from onboarding (Coach, Analyst, …)
    created_at: Mapped[datetime] = _created()


class Subscription(Base):
    """An org's plan (migration 0002); no row = Free. Only ``active`` grants the paid plan."""

    __tablename__ = "subscriptions"

    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True
    )
    plan: Mapped[str] = mapped_column(Text, nullable=False, server_default="free")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")
    minutes_per_month: Mapped[int | None] = mapped_column()
    external_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _created()


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (Index("invites_org_id_idx", "org_id"),)

    id: Mapped[uuid.UUID] = _pk()
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    message: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(TZ)
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        Index("refresh_tokens_family_id_idx", "family_id"),
        Index("refresh_tokens_user_id_idx", "user_id"),
    )

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))  # active org of this session
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZ)  # rotated; presenting it again = reuse
    revoked_at: Mapped[datetime | None] = mapped_column(TZ)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class EmailToken(Base):
    __tablename__ = "email_tokens"
    __table_args__ = (Index("email_tokens_user_id_idx", "user_id"),)

    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)  # verify_email | reset_password
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(TZ, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(TZ)
    created_at: Mapped[datetime] = _created()


class OAuthAccount(Base):
    __tablename__ = "oauth_accounts"
    __table_args__ = (Index("oauth_accounts_user_id_idx", "user_id"),)

    provider: Mapped[str] = mapped_column(Text, primary_key=True)
    subject: Mapped[str] = mapped_column(Text, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = _created()
