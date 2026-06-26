"""Hard OTP rate limits (cooldown + per-phone hourly + per-IP), purpose-tagged."""

from __future__ import annotations

import pytest

from app.config import settings
from app.core.errors import RateLimited
from app.modules.identity import ratelimit


async def test_cooldown_blocks_second_within_window(redis_fake, monkeypatch):
    monkeypatch.setattr(settings, "otp_phone_cooldown_seconds", 60)
    monkeypatch.setattr(settings, "otp_phone_hourly_max", 100)
    monkeypatch.setattr(settings, "otp_ip_hourly_max", 100)
    await ratelimit.check_otp_allowed(redis_fake, "09120000000", "1.1.1.1", "login")
    with pytest.raises(RateLimited):
        await ratelimit.check_otp_allowed(redis_fake, "09120000000", "1.1.1.1", "login")


async def test_per_phone_hourly_cap(redis_fake, monkeypatch):
    monkeypatch.setattr(settings, "otp_phone_cooldown_seconds", 0)  # skip cooldown
    monkeypatch.setattr(settings, "otp_phone_hourly_max", 5)
    monkeypatch.setattr(settings, "otp_ip_hourly_max", 100)
    for _ in range(5):
        await ratelimit.check_otp_allowed(redis_fake, "09120000001", None, "login")
    with pytest.raises(RateLimited):
        await ratelimit.check_otp_allowed(redis_fake, "09120000001", None, "login")


async def test_per_ip_cap(redis_fake, monkeypatch):
    monkeypatch.setattr(settings, "otp_phone_cooldown_seconds", 0)
    monkeypatch.setattr(settings, "otp_phone_hourly_max", 100)
    monkeypatch.setattr(settings, "otp_ip_hourly_max", 3)
    for i in range(3):
        await ratelimit.check_otp_allowed(redis_fake, f"0912000010{i}", "9.9.9.9", "login")
    with pytest.raises(RateLimited):
        await ratelimit.check_otp_allowed(redis_fake, "09120000199", "9.9.9.9", "login")


async def test_purpose_tagged_counters_are_independent(redis_fake, monkeypatch):
    monkeypatch.setattr(settings, "otp_phone_cooldown_seconds", 60)
    monkeypatch.setattr(settings, "otp_phone_hourly_max", 100)
    monkeypatch.setattr(settings, "otp_ip_hourly_max", 100)
    await ratelimit.check_otp_allowed(redis_fake, "09120000002", None, "login")
    # same phone but a different purpose has its own cooldown → allowed
    await ratelimit.check_otp_allowed(redis_fake, "09120000002", None, "reset")
    # second 'login' within the window is still blocked
    with pytest.raises(RateLimited):
        await ratelimit.check_otp_allowed(redis_fake, "09120000002", None, "login")
