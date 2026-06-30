"""Phase-4 WordPress bidirectional sync — service-level (the DONE-WHEN matrix).

Everything runs against the in-memory ``FakeWordPressClient`` + the SQLite identity
DB; no network, no Celery. Covers push, pull, edits both ways, idempotency per
direction, and the loop-prevention guarantee that WP-origin rows are never pushed.
"""

from __future__ import annotations

from app.config import settings
from app.modules.identity import service as identity
from app.modules.wpsync import service as wpsync


async def _local_user(session, phone="09120000001", **kw):
    user = await identity.get_or_create_user(session, phone, source="local")
    for key, value in kw.items():
        setattr(user, key, value)
    await session.flush()
    return user


# 1) A new app signup is pushed and gets a wp_user_id.
async def test_push_new_local_signup(db_session, fake_wp):
    user = await _local_user(db_session, email="a@b.test", full_name="Ali")
    assert await wpsync.push_user(db_session, user.id, client=fake_wp) is True

    refreshed = await identity.get_by_id(db_session, user.id)
    assert refreshed.wp_user_id is not None
    assert refreshed.source == "local"           # origin never flips
    assert refreshed.wp_synced_at is not None
    assert fake_wp.create_count == 1
    wp_rec = await fake_wp.get_user(refreshed.wp_user_id)
    assert wp_rec["meta"][settings.wp_phone_meta_key] == user.phone


# 2) A new WP user is pulled by phone.
async def test_pull_new_wp_user(db_session, fake_wp):
    fake_wp.seed(phone="09121234567", email="w@p.test", name="WP User")
    stats = await wpsync.pull_users(db_session, client=fake_wp)
    assert stats["applied"] == 1

    user = await identity.get_by_phone(db_session, "09121234567")
    assert user is not None
    assert user.source == "wp"
    assert user.email == "w@p.test"
    assert user.wp_user_id is not None


# 3a) An edit on the WP side updates the app by last-modified.
async def test_edit_wp_side_updates_local(db_session, fake_wp):
    rec = fake_wp.seed(
        phone="09121234567", email="old@p.test", modified="2026-06-01T00:00:00+00:00"
    )
    await wpsync.pull_users(db_session, client=fake_wp)

    fake_wp.edit(rec["id"], email="new@p.test", modified="2026-06-20T00:00:00+00:00")
    await wpsync.pull_users(db_session, client=fake_wp)

    user = await identity.get_by_phone(db_session, "09121234567")
    assert user.email == "new@p.test"


# 3b) An edit on the app side updates WP.
async def test_edit_local_side_updates_wp(db_session, fake_wp):
    user = await _local_user(db_session, email="a@b.test")
    await wpsync.push_user(db_session, user.id, client=fake_wp)
    wp_id = (await identity.get_by_id(db_session, user.id)).wp_user_id

    user = await identity.get_by_id(db_session, user.id)
    user.full_name = "Changed"
    await db_session.flush()
    await wpsync.push_user(db_session, user.id, client=fake_wp)

    assert fake_wp.create_count == 1          # no new WP user
    assert fake_wp.update_count >= 1
    assert (await fake_wp.get_user(wp_id))["name"] == "Changed"


# 4a) Re-running the pull is a no-op.
async def test_pull_is_idempotent(db_session, fake_wp):
    fake_wp.seed(
        phone="09121234567", email="w@p.test", modified="2026-06-01T00:00:00+00:00"
    )
    first = await wpsync.pull_users(db_session, client=fake_wp)
    second = await wpsync.pull_users(db_session, client=fake_wp)
    assert first["applied"] == 1
    assert second["applied"] == 0             # nothing newer → zero writes


# 4b) Re-running the push is a no-op (no duplicate WP user).
async def test_push_is_idempotent(db_session, fake_wp):
    user = await _local_user(db_session, email="a@b.test")
    await wpsync.push_user(db_session, user.id, client=fake_wp)
    await wpsync.push_user(db_session, user.id, client=fake_wp)
    assert fake_wp.create_count == 1
    assert len(fake_wp.store) == 1


# 5) Inbound WP users are NOT bounced back to WordPress.
async def test_inbound_not_bounced_back(db_session, fake_wp):
    fake_wp.seed(phone="09121234567", email="w@p.test")
    await wpsync.pull_users(db_session, client=fake_wp)
    writes_before = (fake_wp.create_count, fake_wp.update_count)

    user = await identity.get_by_phone(db_session, "09121234567")
    # Even an explicit, forced push on a WP-origin row is a structural no-op…
    assert await wpsync.push_user(db_session, user.id, client=fake_wp, force=True) is False
    # …and the reconciler never selects it (source='wp', already linked).
    stats = await wpsync.reconcile_pending(db_session, client=fake_wp, force=True)
    assert stats["pushed"] == 0
    assert (fake_wp.create_count, fake_wp.update_count) == writes_before


# 6) Pushing a phone already present in WP links it instead of duplicating.
async def test_push_links_existing_wp_user(db_session, fake_wp):
    seeded = fake_wp.seed(
        phone="09120000009",
        username="09120000009",
        email="09120000009@users.invalid",
    )
    user = await _local_user(db_session, phone="09120000009")
    assert await wpsync.push_user(db_session, user.id, client=fake_wp) is True

    refreshed = await identity.get_by_id(db_session, user.id)
    assert refreshed.wp_user_id == seeded["id"]   # linked, not duplicated
    assert fake_wp.create_count == 0
    assert len(fake_wp.store) == 1


# 7) The reconciler flushes a previously-unpushed local user.
async def test_reconcile_pushes_pending(db_session, fake_wp):
    user = await _local_user(db_session, email="a@b.test")  # local, no wp_user_id
    stats = await wpsync.reconcile_pending(db_session, client=fake_wp)
    assert stats["pending"] == 1 and stats["pushed"] == 1
    assert (await identity.get_by_id(db_session, user.id)).wp_user_id is not None
    # second pass finds nothing pending → idempotent
    again = await wpsync.reconcile_pending(db_session, client=fake_wp)
    assert again["pending"] == 0
