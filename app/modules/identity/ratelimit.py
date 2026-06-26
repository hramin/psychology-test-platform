"""Hard OTP rate limits over Redis (per phone + per IP, purpose-tagged).

Three independent rules enforce the CLAUDE.md limits with simple atomic Redis
primitives (the "token-bucket" intent, expressed as the exact caps):

  1. **cooldown** — at most one request per ``otp_phone_cooldown_seconds`` per
     phone (``SET NX EX``: acquiring the key == permission to send).
  2. **per-phone hourly** — at most ``otp_phone_hourly_max`` per rolling hour
     (fixed-window ``INCR`` + ``EXPIRE``).
  3. **per-IP hourly** — at most ``otp_ip_hourly_max`` per hour per source IP.

All keys are **purpose-tagged** ('login' vs future 'reset') so the two flows are
counted separately. Any rule that trips raises :class:`RateLimited` (HTTP 429).
Each limit is skipped when its configured value is ``<= 0`` (useful in tests).
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.config import settings
from app.core.errors import RateLimited

_HOUR = 3600
_TOO_SOON = "برای دریافت کد جدید کمی صبر کنید."
_TOO_MANY = "درخواست‌های زیادی ثبت شده است. لطفاً بعداً تلاش کنید."


async def _incr_window(redis: aioredis.Redis, key: str, ttl: int) -> int:
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, ttl)
    return count


async def check_otp_allowed(
    redis: aioredis.Redis, phone: str, ip: str | None, purpose: str
) -> None:
    """Raise :class:`RateLimited` if this OTP request exceeds any limit; else
    record it (consumes a cooldown slot + window counters)."""
    cooldown = settings.otp_phone_cooldown_seconds
    if cooldown > 0:
        acquired = await redis.set(
            f"otp:cd:{purpose}:{phone}", "1", nx=True, ex=cooldown
        )
        if not acquired:
            raise RateLimited(_TOO_SOON)

    if settings.otp_phone_hourly_max > 0:
        count = await _incr_window(redis, f"otp:ph:{purpose}:{phone}", _HOUR)
        if count > settings.otp_phone_hourly_max:
            raise RateLimited(_TOO_MANY)

    if ip and settings.otp_ip_hourly_max > 0:
        count = await _incr_window(redis, f"otp:ip:{purpose}:{ip}", _HOUR)
        if count > settings.otp_ip_hourly_max:
            raise RateLimited(_TOO_MANY)


# ── password-login brute-force guard (separate from OTP) ─────────────────────
# Different semantics from OTP (no 60s cooldown — a user may retype a typo): a
# fixed-window *failure* counter per identifier + per IP. ``check`` runs before a
# verify; ``note_password_failure`` ticks only when the password was wrong, so a
# correct password is never throttled.
async def check_password_login_allowed(
    redis: aioredis.Redis, subject: str, ip: str | None
) -> None:
    """Raise :class:`RateLimited` if recent failed password attempts for this
    identifier or source IP exceed the configured caps."""
    sub_max = settings.password_login_subject_max
    if sub_max > 0:
        n = await redis.get(f"pwlogin:sub:{subject}")
        if n is not None and int(n) >= sub_max:
            raise RateLimited(_TOO_MANY)
    if ip and settings.password_login_ip_max > 0:
        n = await redis.get(f"pwlogin:ip:{ip}")
        if n is not None and int(n) >= settings.password_login_ip_max:
            raise RateLimited(_TOO_MANY)


async def note_password_failure(
    redis: aioredis.Redis, subject: str, ip: str | None
) -> None:
    """Record one failed password attempt (per identifier + per source IP)."""
    window = settings.password_login_window_seconds
    if settings.password_login_subject_max > 0:
        await _incr_window(redis, f"pwlogin:sub:{subject}", window)
    if ip and settings.password_login_ip_max > 0:
        await _incr_window(redis, f"pwlogin:ip:{ip}", window)
