"""identity: unique index on users.username (Phase 3 — username/password login)

``users.password_hash`` already exists (added in 0002 as a reserved seam), so the
only schema change Phase 3 needs is making ``username`` uniquely resolvable for
username/password login. A unique index permits multiple NULLs on both Postgres
and SQLite, so OTP-only users without a username are unaffected.

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-26
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_users_username", "users", ["username"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username", table_name="users")
