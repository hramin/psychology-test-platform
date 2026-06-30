"""Admin "sync now" for WordPress sync (Phase 4) — intentionally minimal.

Two admin-gated, CSRF-protected buttons enqueue a pull (WP → app) or a forced push
(reconcile pending local users → WP) onto the ``wpsync`` queue; the workers do the
actual REST I/O off the request path. The full admin panel is Phase 10.
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from app.config import settings
from app.core.templating import templates
from app.deps import admin_required, verify_csrf
from app.modules.identity.models import User

router = APIRouter(prefix="/admin/wpsync", tags=["admin", "wpsync"])


def _csrf(request: Request) -> tuple[str, bool]:
    existing = request.cookies.get(settings.csrf_cookie_name)
    if existing:
        return existing, False
    return secrets.token_urlsafe(32), True


def _render(request: Request, token: str, is_new: bool, message: str | None = None):
    resp = templates.TemplateResponse(
        request,
        "admin/wpsync.html",
        {
            "csrf_token": token,
            "message": message,
            "enabled": settings.wp_sync_enabled,
            "backend": settings.wp_client_backend,
            "pull_mode": settings.wp_pull_mode,
            "push_policy": settings.wp_push_policy,
            "rest_base": settings.wp_rest_base or "—",
        },
    )
    if is_new:
        resp.set_cookie(
            settings.csrf_cookie_name,
            token,
            max_age=settings.session_ttl_seconds,
            httponly=True,
            secure=settings.cookie_secure,
            samesite=settings.cookie_samesite,
            path="/",
        )
    return resp


_DISABLED_MSG = "همگام‌سازی غیرفعال است (WP_SYNC_ENABLED=false)."


@router.get("", response_class=HTMLResponse)
async def admin_page(request: Request, user: User = Depends(admin_required)):
    token, is_new = _csrf(request)
    return _render(request, token, is_new)


@router.post("/pull", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def pull_now(request: Request, user: User = Depends(admin_required)):
    from app.modules.wpsync.tasks import pull_users

    token, is_new = _csrf(request)
    if not settings.wp_sync_enabled:
        return _render(request, token, is_new, _DISABLED_MSG)
    pull_users.apply_async(queue="wpsync")
    return _render(request, token, is_new, "دریافت کاربران از وردپرس در صف قرار گرفت.")


@router.post("/push", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def push_now(request: Request, user: User = Depends(admin_required)):
    from app.modules.wpsync.tasks import reconcile_pushes

    token, is_new = _csrf(request)
    if not settings.wp_sync_enabled:
        return _render(request, token, is_new, _DISABLED_MSG)
    reconcile_pushes.apply_async(kwargs={"force": True}, queue="wpsync")
    return _render(request, token, is_new, "ارسال کاربران محلی به وردپرس در صف قرار گرفت.")
