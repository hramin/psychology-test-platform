"""HTTP layer: login flow, session cookie, CSRF, login_required, logout.

Uses the ``auth_client`` fixture (real ASGI app, fakeredis + SQLite + eager SMS).
"""

from __future__ import annotations

from app.config import settings
from app.modules.identity import otp

CSRF = settings.csrf_cookie_name
SESS = settings.session_cookie_name


async def _do_login(client, monkeypatch, phone="09123456789", code="24680"):
    monkeypatch.setattr(otp, "generate_code", lambda: code)
    r = await client.get("/auth/login")
    assert r.status_code == 200
    token = client.cookies.get(CSRF)
    assert token  # CSRF cookie issued on the login page
    r = await client.post(
        "/auth/otp/request",
        data={"phone": phone, "next": "/auth/profile", "_csrf": token},
    )
    assert r.status_code == 200  # swaps in the code form
    return await client.post(
        "/auth/otp/verify",
        data={"phone": phone, "code": code, "next": "/auth/profile", "_csrf": token},
    )


async def test_full_login_flow_sets_session_and_reaches_profile(auth_client, monkeypatch):
    r = await _do_login(auth_client, monkeypatch)
    assert r.status_code == 204
    assert r.headers.get("hx-redirect") == "/auth/profile"
    assert auth_client.cookies.get(SESS)

    p = await auth_client.get("/auth/profile")
    assert p.status_code == 200
    assert "پروفایل" in p.text


async def test_profile_requires_login(auth_client):
    r = await auth_client.get("/auth/profile", follow_redirects=False)
    assert r.status_code == 303
    assert "/auth/login" in r.headers["location"]


async def test_csrf_mismatch_is_forbidden(auth_client, monkeypatch):
    monkeypatch.setattr(otp, "generate_code", lambda: "13579")
    await auth_client.get("/auth/login")  # issues CSRF cookie
    r = await auth_client.post(
        "/auth/otp/request",
        data={"phone": "09123456789", "next": "/", "_csrf": "WRONG-TOKEN"},
    )
    assert r.status_code == 403


async def test_logout_clears_session(auth_client, monkeypatch):
    await _do_login(auth_client, monkeypatch)
    token = auth_client.cookies.get(CSRF)
    r = await auth_client.post("/auth/logout", data={"_csrf": token})
    assert r.status_code == 204
    # session gone → profile bounces back to login
    p = await auth_client.get("/auth/profile", follow_redirects=False)
    assert p.status_code == 303


async def test_profile_update_persists(auth_client, monkeypatch):
    await _do_login(auth_client, monkeypatch)
    token = auth_client.cookies.get(CSRF)
    r = await auth_client.post(
        "/auth/profile",
        data={"_csrf": token, "username": "ali", "full_name": "علی", "email": ""},
    )
    assert r.status_code == 200
    assert "ali" in r.text
