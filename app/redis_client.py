"""Async Redis client (sessions, OTP, rate limits).

A single module-level client is created from ``settings.redis_url`` and reused
across requests (redis-py manages an internal connection pool, so this is safe
and efficient for the async app). ``decode_responses=True`` so we work with
``str`` values throughout (OTP HMAC hex, session JSON).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import redis.asyncio as aioredis

from app.config import settings

redis_client: aioredis.Redis = aioredis.from_url(
    settings.redis_url, decode_responses=True
)


async def get_redis() -> AsyncIterator[aioredis.Redis]:
    """FastAPI dependency yielding the shared async Redis client."""
    yield redis_client
