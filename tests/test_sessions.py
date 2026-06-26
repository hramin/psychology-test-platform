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


async def test_destroy_removes_sid_from_user_index(redis_fake):
    sid, _ = await sessions.create(redis_fake, 7)
    assert sid in await redis_fake.smembers("user_sess:7")
    await sessions.destroy(redis_fake, sid)
    assert sid not in await redis_fake.smembers("user_sess:7")


async def test_destroy_all_for_user_revokes_every_session(redis_fake):
    sid1, _ = await sessions.create(redis_fake, 99)
    sid2, _ = await sessions.create(redis_fake, 99)
    other, _ = await sessions.create(redis_fake, 100)

    n = await sessions.destroy_all_for_user(redis_fake, 99)
    assert n == 2
    assert await sessions.read(redis_fake, sid1) is None
    assert await sessions.read(redis_fake, sid2) is None
    # a different user's session is untouched
    assert (await sessions.read(redis_fake, other))["user_id"] == 100
