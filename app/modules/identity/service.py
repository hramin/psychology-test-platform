"""Identity service — phone/OTP auth, sessions, users/orgs.

All business rules and authorization live here (not in routes or templates).
Cross-module work goes through other modules' ``service.py``: OTP SMS is sent via
``notifications.service`` (which enqueues the Celery task) — this module never
touches the notifications worker or another module's tables directly.

**The seam:** every login path (OTP now; password in Phase 3; OIDC later) ends at
``establish_session(redis, user)``. That single chokepoint is what makes new login
methods drop in with zero rework.
"""

from __future__ import annotations

import re

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ValidationError
from app.modules.identity import otp, ratelimit, sessions
from app.modules.identity.models import OrgMember, Organization, User
from app.modules.notifications import service as notifications

# ── phone normalization (Iran mobile) ────────────────────────────────────────
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MOBILE_RE = re.compile(r"^09\d{9}$")


def normalize_phone(raw: str) -> str:
    """Normalize to canonical ``09XXXXXXXXX`` or raise ``ValidationError``.

    Accepts Persian/Arabic digits and common prefixes (+98 / 0098 / 98)."""
    if not raw:
        raise ValidationError("شماره تلفن همراه را وارد کنید.")
    digits = re.sub(r"\D", "", raw.translate(_FA_DIGITS))
    if digits.startswith("0098"):
        digits = "0" + digits[4:]
    elif digits.startswith("98") and len(digits) == 12:
        digits = "0" + digits[2:]
    elif digits.startswith("9") and len(digits) == 10:
        digits = "0" + digits
    if not _MOBILE_RE.match(digits):
        raise ValidationError("شماره تلفن همراه معتبر نیست (نمونه: ۰۹۱۲۳۴۵۶۷۸۹).")
    return digits


# ── users ────────────────────────────────────────────────────────────────────
async def get_by_phone(session: AsyncSession, phone: str) -> User | None:
    return (
        await session.execute(select(User).where(User.phone == phone))
    ).scalar_one_or_none()


async def get_or_create_user(
    session: AsyncSession, phone: str, source: str = "local"
) -> User:
    """Idempotent: return the user for ``phone``, creating one if absent. The
    unique ``phone`` constraint is the race backstop (two concurrent first-logins
    → one wins, the other re-reads)."""
    user = await get_by_phone(session, phone)
    if user is not None:
        return user
    user = User(phone=phone, source=source)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        existing = await get_by_phone(session, phone)
        if existing is None:
            raise
        return existing
    return user


async def update_profile(
    session: AsyncSession, user: User, fields: dict
) -> User:
    """Fill the nullable profile fields skipped at signup."""
    for key in ("username", "full_name", "email"):
        if key in fields:
            value = (fields.get(key) or "").strip()
            setattr(user, key, value or None)
    await session.flush()
    return user


async def upsert_admin(
    session: AsyncSession, phone: str, username: str | None = None
) -> User:
    """Create-or-promote the bootstrap admin. Idempotent on phone."""
    phone = normalize_phone(phone)
    user = await get_or_create_user(session, phone, source="local")
    user.is_admin = True
    if username:
        user.username = username.strip() or None
    await session.flush()
    return user


# ── OTP login ─────────────────────────────────────────────────────────────────
async def request_otp(
    redis: aioredis.Redis, raw_phone: str, purpose: str, ip: str | None
) -> str:
    """Rate-limit, mint + store a code, and enqueue the SMS. Returns the
    normalized phone (so the UI can echo where the code was sent). Sends
    regardless of whether the phone has an account (no enumeration)."""
    phone = normalize_phone(raw_phone)
    await ratelimit.check_otp_allowed(redis, phone, ip, purpose)
    code = otp.generate_code()
    await otp.store(redis, phone, purpose, code)
    notifications.send_otp_sms(phone, code, purpose)
    return phone


_OTP_ERRORS = {
    otp.OtpStatus.INVALID: "کد واردشده نادرست است.",
    otp.OtpStatus.EXPIRED: "کد منقضی شده است؛ لطفاً دوباره درخواست کنید.",
    otp.OtpStatus.LOCKED: "تعداد تلاش‌های نادرست زیاد بود؛ کد جدید درخواست کنید.",
}


async def verify_otp_and_login(
    session: AsyncSession,
    redis: aioredis.Redis,
    raw_phone: str,
    code: str,
    purpose: str,
    ip: str | None,
) -> tuple[User, str]:
    """Verify the code and log in. Unknown phone → auto-create (when enabled).
    Returns ``(user, sid)``; the route sets the session cookie."""
    phone = normalize_phone(raw_phone)
    status = await otp.verify(redis, phone, purpose, code)
    if status is not otp.OtpStatus.OK:
        raise ValidationError(_OTP_ERRORS[status])

    if settings.otp_auto_create_users:
        user = await get_or_create_user(session, phone, source="local")
    else:
        user = await get_by_phone(session, phone)
        if user is None:
            raise ValidationError("کاربری با این شماره وجود ندارد.")

    sid = await establish_session(redis, user)
    return user, sid


# ── THE session seam (every login path ends here) ────────────────────────────
async def establish_session(redis: aioredis.Redis, user: User) -> str:
    """Mint a server-side session for ``user`` and return its id (the cookie
    value). Provider-agnostic: OTP login, password login (Phase 3), and a future
    OIDC callback all funnel through this one function."""
    sid, _data = await sessions.create(redis, user.id)
    return sid


async def get_current_user(
    session: AsyncSession, redis: aioredis.Redis, sid: str | None
) -> User | None:
    data = await sessions.read(redis, sid)
    if not data:
        return None
    return await session.get(User, data["user_id"])


async def logout(redis: aioredis.Redis, sid: str | None) -> None:
    await sessions.destroy(redis, sid)


# ── organizations (minimal; billing is Phase 6) ──────────────────────────────
async def create_org(
    session: AsyncSession, name: str, owner: User
) -> Organization:
    org = Organization(name=name, owner_user_id=owner.id)
    session.add(org)
    await session.flush()
    session.add(OrgMember(org_id=org.id, user_id=owner.id, role="owner"))
    await session.flush()
    return org


async def add_member(
    session: AsyncSession, org: Organization, user: User, role: str = "member"
) -> OrgMember:
    member = OrgMember(org_id=org.id, user_id=user.id, role=role)
    session.add(member)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError("این کاربر از قبل عضو سازمان است.") from exc
    return member
