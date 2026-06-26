"""OTP store/verify: success, wrong-code lockout, expiry, purpose isolation."""

from __future__ import annotations

from app.config import settings
from app.modules.identity import otp
from app.modules.identity.otp import OtpStatus


async def test_store_then_verify_ok_is_single_use(redis_fake):
    await otp.store(redis_fake, "09120000000", "login", "12345")
    assert await otp.verify(redis_fake, "09120000000", "login", "12345") is OtpStatus.OK
    # consumed on success → a replay reads as expired
    assert await otp.verify(redis_fake, "09120000000", "login", "12345") is OtpStatus.EXPIRED


async def test_wrong_code_increments_then_locks(redis_fake, monkeypatch):
    monkeypatch.setattr(settings, "otp_max_attempts", 3)
    await otp.store(redis_fake, "09120000000", "login", "11111")
    assert await otp.verify(redis_fake, "09120000000", "login", "00000") is OtpStatus.INVALID
    assert await otp.verify(redis_fake, "09120000000", "login", "00000") is OtpStatus.INVALID
    assert await otp.verify(redis_fake, "09120000000", "login", "00000") is OtpStatus.LOCKED
    # burned: even the correct code no longer works
    assert await otp.verify(redis_fake, "09120000000", "login", "11111") is OtpStatus.EXPIRED


async def test_missing_code_is_expired(redis_fake):
    assert await otp.verify(redis_fake, "09120000000", "login", "12345") is OtpStatus.EXPIRED


async def test_purpose_is_not_cross_usable(redis_fake):
    await otp.store(redis_fake, "09120000000", "login", "12345")
    # a 'login' code cannot verify under 'reset'
    assert await otp.verify(redis_fake, "09120000000", "reset", "12345") is OtpStatus.EXPIRED
    # and the login code is still valid
    assert await otp.verify(redis_fake, "09120000000", "login", "12345") is OtpStatus.OK


async def test_generate_code_length():
    assert len(otp.generate_code()) == settings.otp_code_length
    assert otp.generate_code().isdigit()
