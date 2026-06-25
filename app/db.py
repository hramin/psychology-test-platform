"""Async database setup: engine, session factory, declarative Base.

The app engine targets PgBouncer in **transaction pooling** mode. asyncpg uses
server-side prepared statements which break under transaction pooling unless we
(a) disable asyncpg's statement cache and (b) give every prepared statement a
unique name so names never collide across pooled server connections. This is the
configuration documented by SQLAlchemy for the asyncpg + PgBouncer combination.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings


class Base(AsyncAttrs, DeclarativeBase):
    pass


engine = create_async_engine(
    settings.app_database_url,
    echo=False,
    pool_pre_ping=True,
    connect_args={
        # PgBouncer (transaction mode) compatibility for asyncpg:
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
    },
)

SessionLocal = async_sessionmaker(
    engine, expire_on_commit=False, class_=AsyncSession
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped async session."""
    async with SessionLocal() as session:
        yield session
