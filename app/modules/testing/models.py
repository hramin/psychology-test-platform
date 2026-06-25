"""Testing tables: attempts, scores, interpretations.

Owned exclusively by the testing module. Progress (responses + current page) is
stored on the attempt so the flow is refresh-safe and device-portable and the
app tier stays stateless.

Sensitive columns (responses, raw/T scores, interpretation body) are stored as
plain JSONB in this slice. TODO(Phase 8): encrypt these at rest.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Attempt(Base):
    __tablename__ = "attempts"

    # UUID handle: non-enumerable, since there is no auth gate in this slice.
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    test_version_id: Mapped[int] = mapped_column(
        ForeignKey("test_versions.id"), index=True
    )
    # TODO(Phase 2): user_id once identity exists.
    # TODO(Phase 5): entitlement_id (FK) once the entitlement gate is wired in.
    demographics: Mapped[dict] = mapped_column(JSONB)
    responses: Mapped[dict] = mapped_column(JSONB, default=dict)
    current_page: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(
        String(16), default="in_progress"
    )  # in_progress | completed
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Score(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attempts.id", ondelete="CASCADE"),
        unique=True,
    )
    raw: Mapped[dict] = mapped_column(JSONB)
    t: Mapped[dict] = mapped_column(JSONB)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Interpretation(Base):
    __tablename__ = "interpretations"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("attempts.id", ondelete="CASCADE"),
        unique=True,
    )
    # The AI seam: 'rule' now; an AI worker later writes 'ai' to this same shape.
    source: Mapped[str] = mapped_column(String(16), default="rule")
    body: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
