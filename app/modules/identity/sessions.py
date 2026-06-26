"""Server-side sessions in Redis.

A session is an opaque, unguessable id (the cookie value) mapping to a small JSON
record ``{user_id, csrf, created_at}`` stored at ``sess:{sid}`` with a TTL. The app
stays stateless — all session state lives in Redis, so any web replica can serve
any request.

Each session id is also tracked in a per-user set ``user_sess:{user_id}`` so all
of a user's sessions can be revoked at once — used by password recovery, which
must kill every active session on reset. The set's TTL is refreshed to the
session TTL on each new login; stale ids (whose session already expired) are
harmless, as deleting a missing session key is a no-op.

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
_USER_PREFIX = "user_sess:"


def _new_id() -> str:
    return secrets.token_urlsafe(32)


def _user_key(user_id: int) -> str:
    return f"{_USER_PREFIX}{user_id}"


async def create(redis: aioredis.Redis, user_id: int) -> tuple[str, dict]:
    """Mint a new session for ``user_id``; returns ``(sid, data)``.

    Also indexes the id under ``user_sess:{user_id}`` for bulk revocation.
    (CSRF is handled by a separate double-submit cookie, so it is intentionally
    not stored here.)"""
    sid = _new_id()
    data = {
        "user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    pipe = redis.pipeline()
    pipe.set(_PREFIX + sid, json.dumps(data), ex=settings.session_ttl_seconds)
    pipe.sadd(_user_key(user_id), sid)
    pipe.expire(_user_key(user_id), settings.session_ttl_seconds)
    await pipe.execute()
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
    """Destroy one session and drop it from its user's index."""
    if not sid:
        return
    data = await read(redis, sid)
    pipe = redis.pipeline()
    if data is not None:
        pipe.srem(_user_key(data["user_id"]), sid)
    pipe.delete(_PREFIX + sid)
    await pipe.execute()


async def destroy_all_for_user(redis: aioredis.Redis, user_id: int) -> int:
    """Revoke every active session for ``user_id``; returns how many ids were
    indexed. Called on password reset so old sessions can't outlive the change."""
    key = _user_key(user_id)
    sids = await redis.smembers(key)
    pipe = redis.pipeline()
    for sid in sids:
        pipe.delete(_PREFIX + sid)
    pipe.delete(key)
    await pipe.execute()
    return len(sids)
