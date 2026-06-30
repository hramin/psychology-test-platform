"""identity: WordPress-sync columns (Phase 4)

Adds the two watermarks the bidirectional WordPress sync needs on ``users`` and an
index on the WP join column:

  * ``updated_at`` — local last-modified time (the local side of last-modified-wins).
  * ``wp_synced_at`` — last successful reconcile with WordPress (push or pull).
  * ``ix_users_wp_user_id`` — fast lookup of the linked WP account.

``source`` / ``wp_user_id`` / ``wp_modified_at`` already exist (reserved in 0002).
Both new columns carry a server default so existing rows backfill in place.

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "wp_synced_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_users_wp_user_id", "users", ["wp_user_id"])


def downgrade() -> None:
    op.drop_index("ix_users_wp_user_id", table_name="users")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "wp_synced_at")
