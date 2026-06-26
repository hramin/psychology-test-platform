"""Alembic environment — async, driven by app.config.settings.

Migrations connect to the **direct** Postgres URL (``settings.database_url``),
not PgBouncer, because DDL wants a plain session.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

from app.config import settings
from app.db import Base

# Import models so their tables are registered on Base.metadata.
from app.modules.catalog import models as _catalog_models  # noqa: F401
from app.modules.identity import models as _identity_models  # noqa: F401
from app.modules.testing import models as _testing_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(
        settings.database_url,
        poolclass=pool.NullPool,
        connect_args={"statement_cache_size": 0},
    )
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
