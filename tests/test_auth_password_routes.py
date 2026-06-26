"""HTTP layer for Phase 3: password login, OTP recovery, set/change password, CSRF.

Uses the ``auth_client`` fixture (real ASGI app, fakeredis + SQLite + eager SMS).
"""

from __future__ import annotations

from app.config import settings
from app.modules.identity import otp

CSRF = settings.csrf_cookie_name
SESS = settings.session_cookie_name


async def _otp_login(client, monkeypatch, phone="09120000009", code="11111"):
    """OTP-login once to obtain an authenticated session (returns the CSRF token)."""
    monkeypatch.setattr(otp, "generate_code", lambda: code)
    await client.get("/auth/login")
    token = client.cookies.get(CSRF)
    await client.post(
        "/auth/otp/request",
        data={"phone": phone, "next": "/auth/profile", "_csrf": token},
    )
    r = await client.post(
        "/auth/otp/verify",
        data={"phone": phone, "code": code, "next": "/auth/profile", "_csrf": token},
    )
    assert r.status_code == 204
    return token


async def test_set_password_then_login_with_username(auth_client, monkeypatch):
    token = await _otp_login(auth_client, monkeypatch)
    await auth_client.post(
        "/auth/profile",
        data={"_csrf": token, "username": "navid", "full_name": "", "email": ""},
    )
    r = await auth_client.post(
        "/auth/password", data={"_csrf": token, "new_password": "longenoughpw"}
    )
    assert r.status_code == 200
    assert "موفقیت" in r.text

    await auth_client.post("/auth/logout", data={"_csrf": token})

    await auth_client.get("/auth/login/password")
    token2 = auth_client.cookies.get(CSRF)
    r = await auth_client.post(
        "/auth/login/password",
        data={
            "identifier": "navid", "password": "longenoughpw",
            "next": "/auth/profile", "_csrf": token2,
        },
    )
    assert r.status_code == 204
    assert r.headers.get("hx-redirect") == "/auth/profile"
    assert auth_client.cookies.get(SESS)


async def test_password_login_bad_credentials_is_generic(auth_client):
    await auth_client.get("/auth/login/password")
    token = auth_client.cookies.get(CSRF)
    r = await auth_client.post(
        "/auth/login/password",
        data={"identifier": "ghost", "password": "whatever1", "next": "/", "_csrf": token},
    )
    assert r.status_code == 200            # re-renders the form, no redirect
    assert auth_client.cookies.get(SESS) is None
    assert "نادرست" in r.text


async def test_forgot_then_reset_logs_in_and_new_password_works(
    auth_client, monkeypatch, eager_sms
):
    token = await _otp_login(auth_client, monkeypatch, phone="09120007777")
    await auth_client.post(
        "/auth/profile",
        data={"_csrf": token, "username": "reseteh", "full_name": "", "email": ""},
    )
    await auth_client.post(
        "/auth/password", data={"_csrf": token, "new_password": "originalpw1"}
    )
    await auth_client.post("/auth/logout", data={"_csrf": token})

    monkeypatch.setattr(otp, "generate_code", lambda: "24680")
    await auth_client.get("/auth/forgot")
    token2 = auth_client.cookies.get(CSRF)
    r = await auth_client.post(
        "/auth/forgot",
        data={"identifier": "09120007777", "next": "/auth/profile", "_csrf": token2},
    )
    assert r.status_code == 200
    assert eager_sms[-1]["purpose"] == "reset"

    r = await auth_client.post(
        "/auth/reset",
        data={
            "identifier": "09120007777", "code": "24680",
            "password": "newpass1234", "next": "/auth/profile", "_csrf": token2,
        },
    )
    assert r.status_code == 204            # auto-logged-in via the seam
    assert auth_client.cookies.get(SESS)

    # the new password works on a fresh password login
    await auth_client.post("/auth/logout", data={"_csrf": token2})
    await auth_client.get("/auth/login/password")
    token3 = auth_client.cookies.get(CSRF)
    r = await auth_client.post(
        "/auth/login/password",
        data={
            "identifier": "09120007777", "password": "newpass1234",
            "next": "/auth/profile", "_csrf": token3,
        },
    )
    assert r.status_code == 204


async def test_forgot_unknown_account_identical_and_sends_nothing(auth_client, eager_sms):
    await auth_client.get("/auth/forgot")
    token = auth_client.cookies.get(CSRF)
    r = await auth_client.post(
        "/auth/forgot",
        data={"identifier": "09129999999", "next": "/", "_csrf": token},
    )
    assert r.status_code == 200
    assert "کد بازیابی" in r.text          # same reset form as the existing-account path
    assert eager_sms == []


async def test_reset_requires_csrf(auth_client):
    r = await auth_client.post(
        "/auth/reset",
        data={"identifier": "x", "code": "1", "password": "yyyyyyyy", "_csrf": "WRONG"},
    )
    assert r.status_code == 403
