"""Identity tables: users, organizations, org members.

Owned exclusively by the identity module (accessed only via ``identity.service``).
Column types are deliberately portable (no JSONB/UUID) so the same models create
cleanly on SQLite in the test suite as on PostgreSQL in production.

The auth seams reserved here:
  * ``password_hash`` — NULL now; populated by Phase 3 (username/password).
  * ``external_idp`` / ``external_sub`` — NULL now; the Keycloak/OIDC seam.
  * ``source`` + ``wp_user_id`` / ``wp_modified_at`` — the WordPress-sync seam (Phase 4).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Phone is the canonical identity + the WordPress join key.
    phone: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Unique so it can resolve a single account at username/password login
    # (multiple NULLs are allowed by both Postgres and SQLite).
    username: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    # 'local' (signed up here) | 'wp' (pulled from WordPress in Phase 4).
    source: Mapped[str] = mapped_column(
        String(16), default="local", server_default="local"
    )
    wp_user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    wp_modified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Reserved for later phases (kept nullable on purpose).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    external_idp: Mapped[str | None] = mapped_column(String(64), nullable=True)
    external_sub: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    members: Mapped[list["OrgMember"]] = relationship(
        back_populates="organization", cascade="all, delete-orphan"
    )


class OrgMember(Base):
    __tablename__ = "org_members"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_org_member"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(
        String(16), default="member", server_default="member"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organization: Mapped["Organization"] = relationship(back_populates="members")
