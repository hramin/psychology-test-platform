"""Username/password login at the service layer — incl. enumeration safety and
the brute-force guard. Every login still ends at the establish_session seam."""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.errors import RateLimited, ValidationError
from app.modules.identity import service as identity


async def _user_with_password(
    db_session, phone="09120000001", username="ali", pw="hunter2pw"
):
    user = await identity.get_or_create_user(db_session, phone)
    user.username = username
    await identity.change_password(db_session, user, None, pw)
    await db_session.commit()
    return user


async def test_login_by_username_opens_session(db_session, redis_fake):
    user = await _user_with_password(db_session)
    u, sid = await identity.login_with_password(
        db_session, redis_fake, "ali", "hunter2pw", "1.2.3.4"
    )
    assert u.id == user.id and sid
    resolved = await identity.get_current_user(db_session, redis_fake, sid)
    assert resolved is not None and resolved.id == user.id


async def test_login_by_phone(db_session, redis_fake):
    user = await _user_with_password(db_session)
    u, sid = await identity.login_with_password(
        db_session, redis_fake, "09120000001", "hunter2pw", None
    )
    assert u.id == user.id and sid


async def test_wrong_password_rejected(db_session, redis_fake):
    await _user_with_password(db_session)
    with pytest.raises(ValidationError):
        await identity.login_with_password(
            db_session, redis_fake, "ali", "wrongpass", None
        )


async def test_enumeration_unknown_no_password_and_wrong_share_one_error(
    db_session, redis_fake
):
    """Unknown user, known-but-passwordless user, and wrong password must all
    return the exact same generic message (no account enumeration)."""
    await _user_with_password(db_session)  # 'ali' with a password
    await identity.get_or_create_user(db_session, "09120000002")  # OTP-only, no pw
    await db_session.commit()

    messages = []
    for ident, pw in [
        ("ali", "definitely-wrong"),     # exists, wrong password
        ("09120000002", "anything-1"),   # exists, no password set
        ("nobody", "anything-1"),        # no such account
    ]:
        with pytest.raises(ValidationError) as ei:
            await identity.login_with_password(db_session, redis_fake, ident, pw, None)
        messages.append(str(ei.value))
    assert len(set(messages)) == 1


async def test_first_time_set_needs_no_current_then_login(db_session, redis_fake):
    user = await identity.get_or_create_user(db_session, "09120000003")
    await db_session.commit()
    await identity.change_password(db_session, user, None, "brandnewpw")
    await db_session.commit()
    u, sid = await identity.login_with_password(
        db_session, redis_fake, "09120000003", "brandnewpw", None
    )
    assert u.id == user.id and sid


async def test_change_existing_password_requires_current(db_session, redis_fake):
    user = await _user_with_password(db_session, pw="oldpassword")
    with pytest.raises(ValidationError):
        await identity.change_password(db_session, user, "wrongcurrent", "newpassword")
    await identity.change_password(db_session, user, "oldpassword", "newpassword")
    await db_session.commit()
    # old no longer works; new does
    with pytest.raises(ValidationError):
        await identity.login_with_password(db_session, redis_fake, "ali", "oldpassword", None)
    u, _ = await identity.login_with_password(db_session, redis_fake, "ali", "newpassword", None)
    assert u.id == user.id


async def test_short_password_rejected(db_session):
    user = await identity.get_or_create_user(db_session, "09120000004")
    await db_session.commit()
    with pytest.raises(ValidationError):
        await identity.change_password(db_session, user, None, "x" * (settings.password_min_length - 1))


async def test_brute_force_guard_locks_after_repeated_failures(
    db_session, redis_fake, monkeypatch
):
    monkeypatch.setattr(settings, "password_login_subject_max", 3)
    await _user_with_password(db_session)
    for _ in range(3):
        with pytest.raises(ValidationError):
            await identity.login_with_password(
                db_session, redis_fake, "ali", "wrongpass", "9.9.9.9"
            )
    # locked now — even the correct password is refused (429) within the window
    with pytest.raises(RateLimited):
        await identity.login_with_password(
            db_session, redis_fake, "ali", "hunter2pw", "9.9.9.9"
        )
