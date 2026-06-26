"""Server-side session create/read/destroy."""

from __future__ import annotations

from app.modules.identity import sessions


async def test_create_read_destroy(redis_fake):
    sid, data = await sessions.create(redis_fake, 42)
    assert data["user_id"] == 42
    assert (await sessions.read(redis_fake, sid))["user_id"] == 42
    await sessions.destroy(redis_fake, sid)
    assert await sessions.read(redis_fake, sid) is None


async def test_read_unknown_or_missing_sid(redis_fake):
    assert await sessions.read(redis_fake, "does-not-exist") is None
    assert await sessions.read(redis_fake, None) is None
