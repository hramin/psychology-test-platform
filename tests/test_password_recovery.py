"""OTP-based password recovery at the service layer.

Covers: reset code is purpose 'reset' (never a 'login' code); identical/silent
behavior for non-existing accounts (no enumeration); a correct code sets the
password, revokes ALL sessions, and logs in through the seam; wrong/expired and
login-purpose codes are rejected.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationError
from app.modules.identity import otp, sessions
from app.modules.identity import service as identity


async def _make_user(db_session, phone="09121112233", username="sara", pw="initialpw"):
    user = await identity.get_or_create_user(db_session, phone)
    user.username = username
    await identity.change_password(db_session, user, None, pw)
    await db_session.commit()
    return user


async def test_reset_sends_only_for_existing_account(
    db_session, redis_fake, eager_sms, monkeypatch
):
    monkeypatch.setattr(otp, "generate_code", lambda: "55555")
    await _make_user(db_session)

    await identity.request_password_reset(db_session, redis_fake, "09121112233", "1.1.1.1")
    assert eager_sms[-1]["purpose"] == "reset"
    assert eager_sms[-1]["code"] == "55555"
    sent = len(eager_sms)

    # a phone with no account: identical (no raise) and NOTHING sent
    await identity.request_password_reset(db_session, redis_fake, "09120000000", "1.1.1.2")
    assert len(eager_sms) == sent


async def test_non_existing_request_is_silent(db_session, redis_fake, eager_sms):
    result = await identity.request_password_reset(db_session, redis_fake, "ghost", "1.2.3.4")
    assert result is None
    assert eager_sms == []


async def test_reset_sets_password_and_revokes_all_sessions(
    db_session, redis_fake, eager_sms, monkeypatch
):
    monkeypatch.setattr(otp, "generate_code", lambda: "12345")
    user = await _make_user(db_session)

    # two live sessions before the reset
    sid1, _ = await sessions.create(redis_fake, user.id)
    sid2, _ = await sessions.create(redis_fake, user.id)
    assert await sessions.read(redis_fake, sid1)

    await identity.request_password_reset(db_session, redis_fake, "sara", "1.1.1.1")
    u, new_sid = await identity.reset_password(
        db_session, redis_fake, "sara", "12345", "brandnewpass", "1.1.1.1"
    )
    await db_session.commit()
    assert u.id == user.id

    # every prior session is gone; the fresh one (the seam) works
    assert await sessions.read(redis_fake, sid1) is None
    assert await sessions.read(redis_fake, sid2) is None
    assert (await sessions.read(redis_fake, new_sid))["user_id"] == user.id

    # old password dead, new password works
    with pytest.raises(ValidationError):
        await identity.login_with_password(db_session, redis_fake, "sara", "initialpw", None)
    lu, _ = await identity.login_with_password(
        db_session, redis_fake, "sara", "brandnewpass", None
    )
    assert lu.id == user.id


async def test_wrong_and_unrequested_codes_rejected(
    db_session, redis_fake, eager_sms, monkeypatch
):
    monkeypatch.setattr(otp, "generate_code", lambda: "99999")
    await _make_user(db_session)
    await identity.request_password_reset(db_session, redis_fake, "sara", None)
    # wrong code
    with pytest.raises(ValidationError):
        await identity.reset_password(
            db_session, redis_fake, "sara", "00000", "brandnewpass", None
        )
    # an account that never requested a reset → rejected
    with pytest.raises(ValidationError):
        await identity.reset_password(
            db_session, redis_fake, "09120000000", "99999", "brandnewpass", None
        )


async def test_login_purpose_code_cannot_reset(
    db_session, redis_fake, eager_sms, monkeypatch
):
    """A code minted for 'login' must never satisfy a 'reset' (purpose isolation)."""
    monkeypatch.setattr(otp, "generate_code", lambda: "13579")
    await _make_user(db_session)
    await identity.request_otp(redis_fake, "09121112233", "login", None)  # LOGIN code
    with pytest.raises(ValidationError):
        await identity.reset_password(
            db_session, redis_fake, "sara", "13579", "brandnewpass", None
        )


async def test_reset_rejects_short_password(
    db_session, redis_fake, eager_sms, monkeypatch
):
    monkeypatch.setattr(otp, "generate_code", lambda: "24680")
    await _make_user(db_session)
    await identity.request_password_reset(db_session, redis_fake, "sara", None)
    with pytest.raises(ValidationError):
        await identity.reset_password(db_session, redis_fake, "sara", "24680", "short", None)
