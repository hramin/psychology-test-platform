"""Admin bootstrap: idempotent upsert_admin (the seed-admin command's core)."""

from __future__ import annotations

from sqlalchemy import func, select

from app.modules.identity import service as identity
from app.modules.identity.models import User


async def test_upsert_admin_creates(db_session):
    user = await identity.upsert_admin(db_session, "09121112233", "boss")
    await db_session.commit()
    assert user.is_admin is True
    assert user.username == "boss"
    assert user.phone == "09121112233"


async def test_upsert_admin_idempotent_no_duplicate(db_session):
    await identity.upsert_admin(db_session, "0912 111 2233", "boss")
    await db_session.commit()
    again = await identity.upsert_admin(db_session, "09121112233", None)
    await db_session.commit()
    assert again.is_admin is True
    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.phone == "09121112233")
        )
    ).scalar_one()
    assert count == 1


async def test_upsert_admin_promotes_existing_user(db_session):
    plain = await identity.get_or_create_user(db_session, "09120000009")
    await db_session.commit()
    assert plain.is_admin is False
    admin = await identity.upsert_admin(db_session, "09120000009", None)
    await db_session.commit()
    assert admin.id == plain.id
    assert admin.is_admin is True
