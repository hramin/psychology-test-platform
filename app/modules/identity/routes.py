"""HTMX auth routes: phone/OTP login, logout, and a basic profile.

Two-step login, partial-swapped over HTMX: enter phone → enter the code. Every
mutating form carries a ``_csrf`` token (double-submit cookie). On success the
session cookie is set and the client is sent (``HX-Redirect``) to ``next`` or the
profile page. Business rules live in ``identity.service``; these handlers only
translate HTTP ⇄ service calls.
"""

from __future__ import annotations

import secrets

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError
from app.core.templating import templates
from app.deps import current_user, get_db, get_redis, login_required, verify_csrf
from app.modules.identity import service as identity
from app.modules.identity.models import User
from app.modules.wpsync import service as wpsync

router = APIRouter(prefix="/auth", tags=["auth"])

_DEFAULT_NEXT = "/auth/profile"


# ── small helpers ─────────────────────────────────────────────────────────────
def _client_ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


def _safe_next(raw: str | None) -> str:
    """Only allow same-site, absolute-path redirects (no open redirect)."""
    if raw and raw.startswith("/") and not raw.startswith("//"):
        return raw
    return _DEFAULT_NEXT


def _csrf_for(request: Request) -> tuple[str, bool]:
    existing = request.cookies.get(settings.csrf_cookie_name)
    if existing:
        return existing, False
    return secrets.token_urlsafe(32), True


def _set_cookie(resp: Response, name: str, value: str) -> None:
    resp.set_cookie(
        name, value,
        max_age=settings.session_ttl_seconds,
        httponly=True, secure=settings.cookie_secure,
        samesite=settings.cookie_samesite, path="/",
    )


def _hx_redirect(url: str) -> Response:
    return Response(status_code=204, headers={"HX-Redirect": url})


def _render(request: Request, name: str, ctx: dict, *, set_csrf: tuple[str, bool] | None = None):
    resp = templates.TemplateResponse(request, name, ctx)
    if set_csrf and set_csrf[1]:
        _set_cookie(resp, settings.csrf_cookie_name, set_csrf[0])
    return resp


# ── login (step 1: phone) ─────────────────────────────────────────────────────
@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request, next: str = "", user: User | None = Depends(current_user)
):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    token, is_new = _csrf_for(request)
    return _render(
        request, "auth/login.html",
        {"csrf_token": token, "next": _safe_next(next), "error": None, "phone": ""},
        set_csrf=(token, is_new),
    )


@router.post("/otp/request", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def request_otp(
    request: Request, redis: aioredis.Redis = Depends(get_redis)
):
    form = await request.form()
    phone = (form.get("phone") or "").strip()
    nxt = _safe_next(form.get("next"))
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    try:
        normalized = await identity.request_otp(redis, phone, "login", _client_ip(request))
    except AppError as exc:
        return _render(
            request, "auth/_phone_form.html",
            {"csrf_token": token, "next": nxt, "error": exc.message, "phone": phone},
        )
    return _render(
        request, "auth/_otp_form.html",
        {"csrf_token": token, "next": nxt, "error": None, "phone": normalized},
    )


# ── login (step 2: code) ──────────────────────────────────────────────────────
@router.post("/otp/verify", dependencies=[Depends(verify_csrf)])
async def verify_otp(
    request: Request,
    session: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    form = await request.form()
    phone = (form.get("phone") or "").strip()
    code = (form.get("code") or "").strip()
    nxt = _safe_next(form.get("next"))
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    try:
        _user, sid = await identity.verify_otp_and_login(
            session, redis, phone, code, "login", _client_ip(request)
        )
        await session.commit()
    except AppError as exc:
        return _render(
            request, "auth/_otp_form.html",
            {"csrf_token": token, "next": nxt, "error": exc.message, "phone": phone},
        )
    # New local signup → push to WordPress (off the request path; no-op when sync
    # is off). Already-linked or WP-origin users are left alone (loop-prevention).
    if _user.source == "local" and _user.wp_user_id is None:
        wpsync.enqueue_push_user(_user.id)
    resp = _hx_redirect(nxt)
    _set_cookie(resp, settings.session_cookie_name, sid)
    return resp


# ── password login (parallel to OTP; same establish_session seam) ─────────────
@router.get("/login/password", response_class=HTMLResponse)
async def password_login_page(
    request: Request, next: str = "", user: User | None = Depends(current_user)
):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    token, is_new = _csrf_for(request)
    return _render(
        request, "auth/password_login.html",
        {"csrf_token": token, "next": _safe_next(next), "error": None, "identifier": ""},
        set_csrf=(token, is_new),
    )


@router.post("/login/password", dependencies=[Depends(verify_csrf)])
async def password_login(
    request: Request,
    session: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    form = await request.form()
    identifier = (form.get("identifier") or "").strip()
    nxt = _safe_next(form.get("next"))
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    try:
        _user, sid = await identity.login_with_password(
            session, redis, identifier, form.get("password") or "", _client_ip(request)
        )
    except AppError as exc:
        return _render(
            request, "auth/_password_login_form.html",
            {"csrf_token": token, "next": nxt, "error": exc.message, "identifier": identifier},
        )
    resp = _hx_redirect(nxt)
    _set_cookie(resp, settings.session_cookie_name, sid)
    return resp


# ── forgot password (step 1: request a reset code) ────────────────────────────
@router.get("/forgot", response_class=HTMLResponse)
async def forgot_page(
    request: Request, next: str = "", user: User | None = Depends(current_user)
):
    if user is not None:
        return RedirectResponse(_safe_next(next), status_code=303)
    token, is_new = _csrf_for(request)
    return _render(
        request, "auth/forgot.html",
        {"csrf_token": token, "next": _safe_next(next), "error": None, "identifier": ""},
        set_csrf=(token, is_new),
    )


@router.post("/forgot", dependencies=[Depends(verify_csrf)])
async def forgot_request(
    request: Request,
    session: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    form = await request.form()
    identifier = (form.get("identifier") or "").strip()
    nxt = _safe_next(form.get("next"))
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    try:
        await identity.request_password_reset(
            session, redis, identifier, _client_ip(request)
        )
    except AppError as exc:
        # Only rate-limiting (429) can surface here; account existence never does.
        return _render(
            request, "auth/_forgot_form.html",
            {"csrf_token": token, "next": nxt, "error": exc.message, "identifier": identifier},
        )
    # Identical response whether or not the account exists → no enumeration.
    return _render(
        request, "auth/_reset_form.html",
        {"csrf_token": token, "next": nxt, "error": None, "identifier": identifier},
    )


# ── forgot password (step 2: submit code + new password → logged in) ──────────
@router.post("/reset", dependencies=[Depends(verify_csrf)])
async def reset_submit(
    request: Request,
    session: AsyncSession = Depends(get_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    form = await request.form()
    identifier = (form.get("identifier") or "").strip()
    nxt = _safe_next(form.get("next"))
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    try:
        _user, sid = await identity.reset_password(
            session, redis, identifier,
            form.get("code") or "", form.get("password") or "", _client_ip(request),
        )
        await session.commit()
    except AppError as exc:
        await session.rollback()
        return _render(
            request, "auth/_reset_form.html",
            {"csrf_token": token, "next": nxt, "error": exc.message, "identifier": identifier},
        )
    resp = _hx_redirect(nxt)
    _set_cookie(resp, settings.session_cookie_name, sid)
    return resp


# ── logout ────────────────────────────────────────────────────────────────────
@router.post("/logout", dependencies=[Depends(verify_csrf)])
async def logout(request: Request, redis: aioredis.Redis = Depends(get_redis)):
    sid = request.cookies.get(settings.session_cookie_name)
    await identity.logout(redis, sid)
    resp = _hx_redirect("/auth/login")
    resp.delete_cookie(settings.session_cookie_name, path="/")
    return resp


# ── profile (auth required) ───────────────────────────────────────────────────
@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, user: User = Depends(login_required)):
    token, is_new = _csrf_for(request)
    return _render(
        request, "auth/profile.html",
        {"csrf_token": token, "user": user, "saved": False, "error": None},
        set_csrf=(token, is_new),
    )


@router.post("/profile", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def update_profile(
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(login_required),
):
    form = await request.form()
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    try:
        await identity.update_profile(
            session,
            user,
            {
                "username": form.get("username"),
                "full_name": form.get("full_name"),
                "email": form.get("email"),
            },
        )
        await session.commit()
    except AppError as exc:
        await session.rollback()
        return _render(
            request, "auth/_profile_form.html",
            {"csrf_token": token, "user": user, "saved": False, "error": exc.message},
        )
    # Propagate a local-origin profile edit to WordPress (create if not yet linked,
    # else update). WP-origin users are left to the pull (last-modified-wins).
    if user.source == "local":
        wpsync.enqueue_push_user(user.id)
    return _render(
        request, "auth/_profile_form.html",
        {"csrf_token": token, "user": user, "saved": True, "error": None},
    )


# ── set / change password (auth required) ─────────────────────────────────────
@router.post("/password", response_class=HTMLResponse, dependencies=[Depends(verify_csrf)])
async def change_password(
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(login_required),
):
    form = await request.form()
    token = request.cookies.get(settings.csrf_cookie_name) or ""
    saved, error = False, None
    try:
        await identity.change_password(
            session, user,
            form.get("current_password") or "",
            form.get("new_password") or "",
        )
        await session.commit()
        saved = True
    except AppError as exc:
        await session.rollback()
        error = exc.message
    return _render(
        request, "auth/_password_form.html",
        {"csrf_token": token, "user": user, "saved": saved, "error": error},
    )
