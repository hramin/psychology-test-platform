"""One-time passcodes over Redis — purpose-tagged, hashed, attempt-limited.

The plaintext code is **never** stored: we keep an HMAC (keyed by ``secret_key``,
bound to phone+purpose) with a short TTL, and verify by recomputing it. Codes are
**purpose-tagged** ('login' vs future 'reset') so a code minted for one purpose can
never verify the other (different Redis key *and* different HMAC input).

Pure functions over an async Redis handle → unit-testable with fakeredis.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from enum import Enum

import redis.asyncio as aioredis

from app.config import settings


class OtpStatus(str, Enum):
    OK = "ok"
    INVALID = "invalid"      # wrong code (still within attempt budget)
    EXPIRED = "expired"      # no active code (TTL elapsed or never requested)
    LOCKED = "locked"        # too many wrong attempts → code burned


def _code_key(purpose: str, phone: str) -> str:
    return f"otp:code:{purpose}:{phone}"


def _attempts_key(purpose: str, phone: str) -> str:
    return f"otp:att:{purpose}:{phone}"


def generate_code() -> str:
    """A numeric code of ``settings.otp_code_length`` digits (zero-padded)."""
    n = settings.otp_code_length
    return f"{secrets.randbelow(10 ** n):0{n}d}"


def _hash(purpose: str, phone: str, code: str) -> str:
    msg = f"{purpose}:{phone}:{code}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()


async def store(
    redis: aioredis.Redis, phone: str, purpose: str, code: str
) -> None:
    """Persist the HMAC of a freshly issued code with the configured TTL,
    resetting any prior attempt counter."""
    pipe = redis.pipeline()
    pipe.set(_code_key(purpose, phone), _hash(purpose, phone, code),
             ex=settings.otp_ttl_seconds)
    pipe.delete(_attempts_key(purpose, phone))
    await pipe.execute()


async def verify(
    redis: aioredis.Redis, phone: str, purpose: str, code: str
) -> OtpStatus:
    """Check a submitted code. On success the code is consumed (single-use). On
    too many wrong tries the code is burned (must re-request)."""
    stored = await redis.get(_code_key(purpose, phone))
    if stored is None:
        return OtpStatus.EXPIRED

    if hmac.compare_digest(stored, _hash(purpose, phone, code)):
        await redis.delete(_code_key(purpose, phone), _attempts_key(purpose, phone))
        return OtpStatus.OK

    attempts = await redis.incr(_attempts_key(purpose, phone))
    if attempts == 1:
        # keep the counter's lifetime aligned with the code's
        await redis.expire(_attempts_key(purpose, phone), settings.otp_ttl_seconds)
    if attempts >= settings.otp_max_attempts:
        await redis.delete(_code_key(purpose, phone), _attempts_key(purpose, phone))
        return OtpStatus.LOCKED
    return OtpStatus.INVALID
