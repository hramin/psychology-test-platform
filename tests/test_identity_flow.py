"""Full OTP flow at the service layer: request → eager SMS → verify → session."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.core.errors import ValidationError
from app.modules.identity import otp
from app.modules.identity import service as identity
from app.modules.identity.models import User


async def test_request_then_verify_creates_user_and_session(
    db_session, redis_fake, eager_sms, monkeypatch
):
    monkeypatch.setattr(otp, "generate_code", lambda: "54321")

    # request: rate-limit ok, code stored, SMS sent inline via the mock backend
    phone = await identity.request_otp(redis_fake, "0912 345 6789", "login", "1.2.3.4")
    assert phone == "09123456789"
    assert eager_sms[-1] == {
        "receptor": "09123456789",
        "code": "54321",
        "purpose": "login",
        "message": "کد ورود شما: 54321",
    }

    # verify: unknown phone → auto-created source='local', session minted
    user, sid = await identity.verify_otp_and_login(
        db_session, redis_fake, "09123456789", "54321", "login", "1.2.3.4"
    )
    await db_session.commit()
    assert user.source == "local" and user.is_admin is False
    assert user.username is None  # filled later on the profile page

    resolved = await identity.get_current_user(db_session, redis_fake, sid)
    assert resolved is not None and resolved.id == user.id


async def test_auto_create_is_idempotent_per_phone(db_session):
    a = await identity.get_or_create_user(db_session, "09120001111")
    await db_session.commit()
    b = await identity.get_or_create_user(db_session, "09120001111")
    await db_session.commit()
    assert a.id == b.id
    count = (
        await db_session.execute(
            select(func.count()).select_from(User).where(User.phone == "09120001111")
        )
    ).scalar_one()
    assert count == 1


async def test_wrong_code_does_not_log_in(
    db_session, redis_fake, eager_sms, monkeypatch
):
    monkeypatch.setattr(otp, "generate_code", lambda: "11111")
    await identity.request_otp(redis_fake, "09123456789", "login", None)
    with pytest.raises(ValidationError):
        await identity.verify_otp_and_login(
            db_session, redis_fake, "09123456789", "99999", "login", None
        )


async def test_reject_unknown_phone_when_auto_create_off(
    db_session, redis_fake, eager_sms, monkeypatch
):
    from app.config import settings

    monkeypatch.setattr(settings, "otp_auto_create_users", False)
    monkeypatch.setattr(otp, "generate_code", lambda: "22222")
    await identity.request_otp(redis_fake, "09123456789", "login", None)
    with pytest.raises(ValidationError):
        await identity.verify_otp_and_login(
            db_session, redis_fake, "09123456789", "22222", "login", None
        )


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("09123456789", "09123456789"),
        ("۰۹۱۲۳۴۵۶۷۸۹", "09123456789"),
        ("+98 912 345 6789", "09123456789"),
        ("00989123456789", "09123456789"),
    ],
)
def test_normalize_phone(raw, expected):
    assert identity.normalize_phone(raw) == expected


def test_normalize_phone_rejects_garbage():
    with pytest.raises(ValidationError):
        identity.normalize_phone("12345")
