"""Shared FastAPI dependencies: DB session, Redis, current user, CSRF.

Authorization logic lives in ``identity.service`` — these dependencies are thin
adapters that resolve the session cookie to a user and enforce CSRF on mutating
requests.
"""

from __future__ import annotations

import secrets

import redis.asyncio as aioredis
from fastapi import Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AuthRequired, Forbidden
from app.db import get_session
from app.modules.identity import service as identity
from app.modules.identity.models import User
from app.redis_client import get_redis

# Stable import points for route modules.
get_db = get_session

__all__ = [
    "get_db",
    "get_redis",
    "current_user",
    "login_required",
    "admin_required",
    "issue_csrf",
    "verify_csrf",
]


# ── current user / auth gate ─────────────────────────────────────────────────
async def current_user(
    request: Request,
    session: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
) -> User | None:
    """Resolve the session cookie → user, or ``None`` if unauthenticated."""
    sid = request.cookies.get(settings.session_cookie_name)
    return await identity.get_current_user(session, redis, sid)


async def login_required(
    user: User | None = Depends(current_user),
) -> User:
    """Require an authenticated user. Raises ``AuthRequired`` (→ redirect to the
    login page for HTML, 401 for the API)."""
    if user is None:
        raise AuthRequired("برای دسترسی به این صفحه باید وارد شوید.")
    return user


async def admin_required(
    user: User = Depends(login_required),
) -> User:
    """Require an authenticated **admin**. Builds on ``login_required`` so an
    anonymous visitor is redirected to login, while a logged-in non-admin gets 403."""
    if not user.is_admin:
        raise Forbidden("این بخش تنها برای مدیران در دسترس است.")
    return user


# ── CSRF (double-submit cookie) ──────────────────────────────────────────────
def issue_csrf(request: Request, response: Response) -> str:
    """Return the CSRF token for embedding in a form, setting the cookie if
    absent. Templates render it as a hidden ``_csrf`` field; ``verify_csrf``
    compares the submitted value to this cookie."""
    token = request.cookies.get(settings.csrf_cookie_name)
    if not token:
        token = secrets.token_urlsafe(32)
        response.set_cookie(
            settings.csrf_cookie_name,
            token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            path="/",
        )
    return token


async def verify_csrf(request: Request) -> None:
    """Enforce CSRF on a mutating request: the ``_csrf`` form field must match the
    ``pst_csrf`` cookie. SameSite=Lax already blocks cross-site form POSTs; this is
    defense in depth."""
    cookie = request.cookies.get(settings.csrf_cookie_name)
    form = await request.form()
    submitted = form.get("_csrf")
    if not cookie or not submitted or not secrets.compare_digest(cookie, str(submitted)):
        raise Forbidden("نشست شما منقضی شده است؛ صفحه را تازه‌سازی کنید.")
