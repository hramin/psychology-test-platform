"""Celery tasks for the `wpsync` queue — WordPress sync, off the request path.

The web tier only *enqueues* (``service.enqueue_push_user``); the REST round-trips
happen here. Beat drives the periodic pull + reconcile (see ``celery_app``).

Each task runs its coroutine on a **fresh engine** created and disposed per run:
Celery's prefork worker calls ``asyncio.run`` per task (a new event loop each time),
and an asyncpg pool is bound to the loop that created it, so a shared engine would
leak connections across loops. A short-lived engine sidesteps that cleanly (the
sync cadence is minutes, so the setup cost is irrelevant).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.celery_app import celery_app
from app.config import settings
from app.modules.wpsync import service
from app.modules.wpsync.client import WordPressError

log = logging.getLogger("wpsync.tasks")


@asynccontextmanager
async def _worker_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(
        settings.app_database_url,
        pool_pre_ping=True,
        connect_args={
            "statement_cache_size": 0,
            "prepared_statement_cache_size": 0,
            "prepared_statement_name_func": lambda: f"__asyncpg_{uuid.uuid4()}__",
        },
    )
    try:
        Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with Session() as session:
            yield session
    finally:
        await engine.dispose()


@celery_app.task(name="wpsync.push_user_to_wordpress")
def push_user_to_wordpress(user_id: int) -> None:
    """Push one local user to WordPress (event-driven, enqueued on local signup/edit).
    A REST failure is logged and swallowed — the row stays unlinked, so the Beat
    reconciler retries it. That is the forward-push retry mechanism."""
    if not settings.wp_sync_enabled:
        return

    async def _run() -> None:
        async with _worker_session() as session:
            await service.push_user(session, user_id)

    try:
        asyncio.run(_run())
    except WordPressError as exc:
        log.warning("push task for user_id=%s failed: %s (reconciler will retry)", user_id, exc)


@celery_app.task(name="wpsync.pull_users")
def pull_users() -> None:
    """Read sync (WP → app). Scheduled by Beat every ``WP_SYNC_INTERVAL_MINUTES``."""
    if not settings.wp_sync_enabled:
        return

    async def _run() -> None:
        async with _worker_session() as session:
            await service.pull_users(session)

    asyncio.run(_run())


@celery_app.task(name="wpsync.reconcile_pushes")
def reconcile_pushes(force: bool = False) -> None:
    """Retry forward pushes that never linked. Beat schedules it every
    ``WP_RECONCILE_INTERVAL_MINUTES`` with ``force=False`` (respects
    ``WP_PUSH_POLICY``); the admin "push now" passes ``force=True`` to flush pending
    even under the ``defer``/``manual`` policies."""
    if not settings.wp_sync_enabled:
        return

    async def _run() -> None:
        async with _worker_session() as session:
            await service.reconcile_pending(session, force=force)

    asyncio.run(_run())
