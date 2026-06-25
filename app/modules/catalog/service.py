"""Catalog service — the ONLY way other modules read catalog data.

A test_version's ``definition`` is immutable once written, so it is safe to cache
in-process keyed by version id (correct even across replicas). The active version
for a slug is cached too; the seeder calls ``invalidate()`` after writing so the
cache never serves a stale active version. Redis becomes the cross-process cache
in a later phase; in-process is sufficient and simpler for this slice.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.catalog.models import Test, TestVersion

# slug -> (test_version_id, definition)
_ACTIVE_CACHE: dict[str, tuple[int, dict]] = {}
# test_version_id -> definition  (immutable, so always safe)
_DEFINITION_BY_VERSION: dict[int, dict] = {}


async def get_active_version(session: AsyncSession, slug: str) -> TestVersion | None:
    stmt = (
        select(TestVersion)
        .join(Test, Test.id == TestVersion.test_id)
        .where(
            Test.slug == slug,
            Test.is_active.is_(True),
            TestVersion.is_active.is_(True),
        )
        .order_by(TestVersion.version.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalars().first()


async def get_active(session: AsyncSession, slug: str) -> tuple[int, dict] | None:
    """Return ``(test_version_id, definition)`` for the active version of a test."""
    cached = _ACTIVE_CACHE.get(slug)
    if cached is not None:
        return cached
    tv = await get_active_version(session, slug)
    if tv is None:
        return None
    _ACTIVE_CACHE[slug] = (tv.id, tv.definition)
    _DEFINITION_BY_VERSION[tv.id] = tv.definition
    return _ACTIVE_CACHE[slug]


async def get_definition_for_version(
    session: AsyncSession, version_id: int
) -> dict | None:
    """Definition pinned to a specific version (used by an in-progress attempt)."""
    cached = _DEFINITION_BY_VERSION.get(version_id)
    if cached is not None:
        return cached
    tv = await session.get(TestVersion, version_id)
    if tv is None:
        return None
    _DEFINITION_BY_VERSION[version_id] = tv.definition
    return tv.definition


def invalidate(slug: str | None = None) -> None:
    """Clear caches (call after seeding / activating a version)."""
    if slug is None:
        _ACTIVE_CACHE.clear()
    else:
        _ACTIVE_CACHE.pop(slug, None)
    _DEFINITION_BY_VERSION.clear()
