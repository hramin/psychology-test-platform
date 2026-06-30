"""HTTP wiring for Phase 4: the signup→push enqueue seam + admin gating.

Uses the ``auth_client`` fixture (real ASGI app, fakeredis + SQLite + eager SMS).
"""

from __future__ import annotations

from app.config import settings
from app.modules.identity import otp

CSRF = settings.csrf_cookie_name


async def _signup(client, monkeypatch, phone, code="24680"):
    monkeypatch.setattr(otp, "generate_code", lambda: code)
    await client.get("/auth/login")
    token = client.cookies.get(CSRF)
    await client.post(
        "/auth/otp/request",
        data={"phone": phone, "next": "/auth/profile", "_csrf": token},
    )
    return await client.post(
        "/auth/otp/verify",
        data={"phone": phone, "code": code, "next": "/auth/profile", "_csrf": token},
    )


async def test_new_signup_enqueues_one_push(auth_client, monkeypatch):
    """A brand-new local signup enqueues exactly one push (the event-driven path)."""
    calls: list[int] = []
    from app.modules.wpsync import service as wpsync

    monkeypatch.setattr(wpsync, "enqueue_push_user", lambda uid: calls.append(uid))

    r = await _signup(auth_client, monkeypatch, "09120009999")
    assert r.status_code == 204
    assert len(calls) == 1  # one push enqueued for the new user


async def test_admin_wpsync_requires_login(auth_client):
    r = await auth_client.get("/admin/wpsync", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/login" in r.headers["location"]


async def test_admin_wpsync_forbidden_for_non_admin(auth_client, monkeypatch):
    # A normal (non-admin) signup must not reach the admin sync page.
    await _signup(auth_client, monkeypatch, "09123334444", code="13579")
    r = await auth_client.get("/admin/wpsync", follow_redirects=False)
    assert r.status_code == 403
