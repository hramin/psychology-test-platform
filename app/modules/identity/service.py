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

import logging
import re
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import AppError, ValidationError
from app.modules.identity import otp, passwords, ratelimit, sessions
from app.modules.identity.models import OrgMember, Organization, User
from app.modules.notifications import service as notifications

log = logging.getLogger("identity.service")

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
async def get_by_id(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_by_phone(session: AsyncSession, phone: str) -> User | None:
    return (
        await session.execute(select(User).where(User.phone == phone))
    ).scalar_one_or_none()


async def get_by_username(session: AsyncSession, username: str) -> User | None:
    """Exact-match lookup (username is unique). Empty/blank → ``None``."""
    username = (username or "").strip()
    if not username:
        return None
    return (
        await session.execute(select(User).where(User.username == username))
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
    """Fill the nullable profile fields skipped at signup. ``username`` is unique;
    a clash with another account is reported (the DB index is the race backstop)."""
    if "username" in fields:
        new_username = (fields.get("username") or "").strip() or None
        if new_username and new_username != user.username:
            clash = await get_by_username(session, new_username)
            if clash is not None and clash.id != user.id:
                raise ValidationError("این نام کاربری قبلاً انتخاب شده است.")
        user.username = new_username
    for key in ("full_name", "email"):
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


# ── WordPress sync support (Phase 4) ──────────────────────────────────────────
# These are the ONLY entry points the wpsync module uses to read/write users (it
# never touches the table directly). None of them enqueues a push — that keeps the
# inbound pull path structurally incapable of bouncing a record back to WordPress.
def _as_aware_utc(dt: datetime | None) -> datetime | None:
    """Normalize to aware-UTC for safe comparison (SQLite drops tzinfo on read)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


async def get_by_wp_user_id(
    session: AsyncSession, wp_user_id: int
) -> User | None:
    return (
        await session.execute(
            select(User).where(User.wp_user_id == wp_user_id)
        )
    ).scalar_one_or_none()


async def upsert_from_wordpress(
    session: AsyncSession,
    *,
    phone: str,
    wp_user_id: int | None,
    wp_modified_at: datetime | None,
    email: str | None = None,
    full_name: str | None = None,
) -> tuple[User, bool]:
    """Inbound upsert by phone (the join key), resolving conflicts last-modified-wins.

    Returns ``(user, changed)``. A brand-new phone creates a ``source='wp'`` row.
    For an existing row we apply WP's fields only when WP is the newer side (its
    ``wp_modified_at`` is ahead of what we last stored, or no timestamp is available
    — in which case WP, being authoritative for its own records, wins on a real
    difference). Re-running with no WP change is a no-op (``changed=False``); a row
    we just pushed and that echoes back here is recognized and left untouched.
    """
    now = datetime.now(timezone.utc)
    user = None
    if wp_user_id is not None:
        user = await get_by_wp_user_id(session, wp_user_id)
    if user is None:
        user = await get_by_phone(session, phone)

    if user is None:
        user = User(
            phone=phone,
            source="wp",
            wp_user_id=wp_user_id,
            wp_modified_at=wp_modified_at,
            wp_synced_at=now,
            email=email,
            full_name=full_name,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError:  # concurrent insert on the same phone
            await session.rollback()
            existing = await get_by_phone(session, phone)
            if existing is None:
                raise
            user = existing
        else:
            return user, True

    changed = False
    if wp_user_id is not None and user.wp_user_id is None:
        user.wp_user_id = wp_user_id
        changed = True
    # WP wins when it is the newer side (or when we have no timestamp to compare).
    # Coerce both sides to aware-UTC first: SQLite reads DateTime(timezone=True)
    # back as naive, so a raw compare with the aware inbound value would raise.
    stored_mod = _as_aware_utc(user.wp_modified_at)
    incoming_mod = _as_aware_utc(wp_modified_at)
    wp_wins = stored_mod is None or incoming_mod is None or incoming_mod > stored_mod
    if wp_wins:
        if email is not None and email != user.email:
            user.email = email
            changed = True
        if full_name is not None and full_name != user.full_name:
            user.full_name = full_name
            changed = True
        if incoming_mod is not None and incoming_mod != stored_mod:
            user.wp_modified_at = wp_modified_at
            changed = True
    if changed:
        user.wp_synced_at = now
        await session.flush()
    return user, changed


async def mark_wp_pushed(
    session: AsyncSession,
    user: User,
    *,
    wp_user_id: int,
    wp_modified_at: datetime | None = None,
    synced_at: datetime | None = None,
) -> User:
    """Record the result of a successful outbound push: link the WP id and stamp
    the sync watermark (so the next pull recognizes the echo and does nothing)."""
    user.wp_user_id = wp_user_id
    if wp_modified_at is not None:
        user.wp_modified_at = wp_modified_at
    user.wp_synced_at = synced_at or datetime.now(timezone.utc)
    await session.flush()
    return user


async def list_pending_wp_push(
    session: AsyncSession, limit: int = 200
) -> list[User]:
    """Local-origin users not yet linked to a WP account — the reconciler's work
    list (retries pushes that failed or were never delivered)."""
    rows = await session.execute(
        select(User)
        .where(User.source == "local", User.wp_user_id.is_(None))
        .order_by(User.id)
        .limit(limit)
    )
    return list(rows.scalars().all())


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


async def invalidate_user_sessions(redis: aioredis.Redis, user: User) -> int:
    """Revoke every active session for ``user`` (used after a password reset)."""
    return await sessions.destroy_all_for_user(redis, user.id)


# ── passwords: set/change, login, recovery (Phase 3) ─────────────────────────
# All three credential paths live here (never in routes/templates); every
# successful one ends at the same ``establish_session`` seam as OTP login.
_BAD_CREDENTIALS = "نام کاربری یا گذرواژه نادرست است."
_BAD_RESET = "کد بازیابی نامعتبر یا منقضی شده است."
_WRONG_CURRENT = "گذرواژه فعلی نادرست است."


def _weak_password_msg() -> str:
    return f"گذرواژه باید دست‌کم {settings.password_min_length} نویسه باشد."


async def _resolve_identifier(
    session: AsyncSession, raw: str
) -> tuple[str, User | None, str | None]:
    """Map a typed identifier (phone *or* username) → ``(subject, user, phone)``.

    ``subject`` is a rate-limit key derived purely from the input — the normalized
    phone when it parses as one, else the case-folded username — so limiting never
    depends on whether the account exists (no enumeration). ``user``/``phone`` are
    ``None`` when nothing matches."""
    raw = (raw or "").strip()
    try:
        phone = normalize_phone(raw)
    except ValidationError:
        phone = None
    if phone is not None:
        user = await get_by_phone(session, phone)
        return phone, user, phone
    user = await get_by_username(session, raw)
    return raw.casefold(), user, (user.phone if user is not None else None)


async def change_password(
    session: AsyncSession,
    user: User,
    current_password: str | None,
    new_password: str,
) -> User:
    """Set or change ``user``'s password while authenticated. Changing an existing
    password requires the correct current one; a first-time set (OTP-only user
    with no hash yet) does not."""
    if len(new_password or "") < settings.password_min_length:
        raise ValidationError(_weak_password_msg())
    if user.password_hash and not passwords.verify_password(
        user.password_hash, current_password or ""
    ):
        raise ValidationError(_WRONG_CURRENT)
    user.password_hash = passwords.hash_password(new_password)
    await session.flush()
    log.info("password set/changed for user_id=%s", user.id)
    return user


async def login_with_password(
    session: AsyncSession,
    redis: aioredis.Redis,
    identifier: str,
    password: str,
    ip: str | None,
) -> tuple[User, str]:
    """Authenticate by username-or-phone + password, then open a session. Every
    failure mode returns one identical error (and burns equivalent CPU) so the
    response never reveals whether the account exists or has a password set."""
    subject, user, _phone = await _resolve_identifier(session, identifier)
    await ratelimit.check_password_login_allowed(redis, subject, ip)
    stored = user.password_hash if user is not None else None
    if not passwords.verify_password(stored, password or ""):
        if not stored:  # no user / no password → spend the same time regardless
            passwords.verify_password(passwords.DUMMY_HASH, password or "")
        await ratelimit.note_password_failure(redis, subject, ip)
        raise ValidationError(_BAD_CREDENTIALS)
    sid = await establish_session(redis, user)
    return user, sid


async def request_password_reset(
    session: AsyncSession,
    redis: aioredis.Redis,
    identifier: str,
    ip: str | None,
) -> None:
    """Issue a purpose-'reset' OTP **iff** the identifier maps to a real account
    with a phone. Rate-limited on the input-derived subject and silent about the
    outcome, so callers render an identical screen either way (no enumeration).
    The 'reset' code is never cross-usable with a 'login' code."""
    subject, user, phone = await _resolve_identifier(session, identifier)
    await ratelimit.check_otp_allowed(redis, subject, ip, "reset")
    if user is not None and phone:
        code = otp.generate_code()
        await otp.store(redis, phone, "reset", code)
        notifications.send_otp_sms(phone, code, "reset")
        log.info("password reset requested for user_id=%s", user.id)


async def reset_password(
    session: AsyncSession,
    redis: aioredis.Redis,
    identifier: str,
    code: str,
    new_password: str,
    ip: str | None,
) -> tuple[User, str]:
    """Complete recovery: verify the 'reset' code, set the new password,
    invalidate ALL of the user's sessions, then open a fresh one (login through
    the seam). A 'login'-purpose code can't satisfy this (different Redis key +
    HMAC). Every failure returns one generic error (no enumeration)."""
    if len(new_password or "") < settings.password_min_length:
        raise ValidationError(_weak_password_msg())
    _subject, user, phone = await _resolve_identifier(session, identifier)
    status = (
        await otp.verify(redis, phone, "reset", code or "")
        if user is not None and phone
        else otp.OtpStatus.EXPIRED
    )
    if status is not otp.OtpStatus.OK:
        raise ValidationError(_BAD_RESET)
    user.password_hash = passwords.hash_password(new_password)
    await session.flush()
    await invalidate_user_sessions(redis, user)
    sid = await establish_session(redis, user)
    log.info("password reset completed for user_id=%s (all sessions revoked)", user.id)
    return user, sid


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
