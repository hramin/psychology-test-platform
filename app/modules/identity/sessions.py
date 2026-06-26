"""Server-side sessions in Redis.

A session is an opaque, unguessable id (the cookie value) mapping to a small JSON
record ``{user_id, csrf, created_at}`` stored at ``sess:{sid}`` with a TTL. The app
stays stateless — all session state lives in Redis, so any web replica can serve
any request.

These are low-level primitives; the ``establish_session`` *seam* in
``identity.service`` is what login paths call.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone

import redis.asyncio as aioredis

from app.config import settings

_PREFIX = "sess:"


def _new_id() -> str:
    return secrets.token_urlsafe(32)


async def create(redis: aioredis.Redis, user_id: int) -> tuple[str, dict]:
    """Mint a new session for ``user_id``; returns ``(sid, data)``.

    (CSRF is handled by a separate double-submit cookie, so it is intentionally
    not stored here.)"""
    sid = _new_id()
    data = {
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await redis.set(_PREFIX + sid, json.dumps(data), ex=settings.session_ttl_seconds)
    return sid, data


async def read(redis: aioredis.Redis, sid: str | None) -> dict | None:
    if not sid:
        return None
    raw = await redis.get(_PREFIX + sid)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


async def destroy(redis: aioredis.Redis, sid: str | None) -> None:
    if sid:
        await redis.delete(_PREFIX + sid)
