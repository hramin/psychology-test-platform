"""Pure mapping between WordPress user dicts and local ``User`` fields.

No I/O and no app-state here — just translation + the phone normalization that
makes phone a reliable join key. Reuses ``identity.service.normalize_phone`` so the
WP side and the local side agree on canonical ``09XXXXXXXXX`` form.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from app.config import settings
from app.core.errors import ValidationError
from app.modules.identity.service import normalize_phone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _first(value):
    """WP single-value meta can come back as a scalar or a 1-element list."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def extract_phone(wp_user: dict) -> str | None:
    """Normalized join-key phone from a WP user's exposed usermeta, or ``None``
    (missing/unparseable → the caller skips the row; we never guess)."""
    raw = _first((wp_user.get("meta") or {}).get(settings.wp_phone_meta_key))
    if not raw:
        return None
    try:
        return normalize_phone(str(raw))
    except ValidationError:
        return None


def extract_modified(wp_user: dict) -> datetime | None:
    """The WP-side last-modified timestamp for last-modified-wins. Prefers the
    mu-plugin's ``pst_modified_gmt`` meta; falls back to core modified/registered."""
    raw = _first((wp_user.get("meta") or {}).get(settings.wp_modified_meta_key))
    return _parse_dt(raw) or _parse_dt(
        wp_user.get("modified_gmt") or wp_user.get("registered_date")
    )


def wp_to_local_fields(wp_user: dict) -> dict:
    """Profile fields mirrored locally from WP (phone is resolved separately)."""
    email = (wp_user.get("email") or "").strip() or None
    name = (wp_user.get("name") or "").strip() or None
    return {"email": email, "full_name": name}


def synthesize_credentials(user) -> tuple[str, str, str]:
    """``(username, email, password)`` for pushing a local user to WP.

    Fills a missing username/email from the phone (``WP_PUSH_POLICY='synthesize'``)
    so WP's required-and-unique fields are always satisfied, and always returns a
    fresh random password — this app stays the identity source; the WP password is
    just there to satisfy the create.
    """
    username = (user.username or "").strip() or user.phone
    email = (user.email or "").strip() or (
        f"{user.phone}@{settings.wp_placeholder_email_domain}"
    )
    return username, email, secrets.token_urlsafe(24)


def can_push(user) -> bool:
    """Whether the configured push policy permits auto-pushing this user now."""
    policy = settings.wp_push_policy
    if policy == "manual":
        return False
    if policy == "defer":
        # Only once the user carries a real email (no placeholder lands in WP).
        return bool((user.email or "").strip())
    return True  # 'synthesize'
