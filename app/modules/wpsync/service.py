"""WordPress sync orchestration + the enqueue seam.

Three operations — ``push_user`` (app → WP), ``pull_users`` (WP → app), and
``reconcile_pending`` (retry unfinished pushes) — plus ``enqueue_push_user``, the
thin seam ``identity.routes`` calls after committing a local-origin write (exactly
how it calls ``notifications.service`` for OTP SMS).

Boundaries: all user-table reads/writes go through ``identity.service``; all
WordPress I/O goes through a ``WordPressClient``. **The pull path never enqueues a
push and ``source='wp'`` rows are never pushed** — that pair is the structural
loop-prevention. Idempotency comes from last-modified-wins (inbound) and
create-or-update keyed on ``wp_user_id`` (outbound).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.modules.identity import service as identity
from app.modules.identity.models import User
from app.modules.wpsync import mapping
from app.modules.wpsync.client import (
    WordPressClient,
    WordPressError,
    WordPressUserExists,
    get_wp_client,
)

log = logging.getLogger("wpsync.service")


# ── enqueue seam (identity.routes calls this after commit) ────────────────────
def enqueue_push_user(user_id: int) -> None:
    """Enqueue an outbound push for a local-origin user. No-op when sync is off, so
    dev/test never hit the network unless explicitly enabled. Thin like
    ``notifications.send_otp_sms`` — the worker does the actual REST round-trip."""
    if not settings.wp_sync_enabled:
        return
    from app.modules.wpsync.tasks import push_user_to_wordpress  # lazy → no import cycle

    push_user_to_wordpress.apply_async(args=[user_id], queue="wpsync")


# ── push (app → WP) ───────────────────────────────────────────────────────────
async def push_user(
    session: AsyncSession,
    user_id: int,
    *,
    client: WordPressClient | None = None,
    force: bool = False,
) -> bool:
    """Create-or-update one local user in WordPress. Returns True if a write was made.

    ``source='wp'`` rows are NEVER pushed (loop guard, not overridable). ``force``
    bypasses only the *policy* gate (``defer``/``manual``) for explicit operator
    actions (admin "sync now", CLI); the periodic signup/reconcile paths leave it
    False so they respect ``WP_PUSH_POLICY``.
    """
    user = await identity.get_by_id(session, user_id)
    if user is None:
        log.info("push skipped: user_id=%s not found (will retry via reconcile)", user_id)
        return False
    if user.source == "wp":
        return False  # WP-owned record → pull is authoritative; never bounce back
    if not force and not mapping.can_push(user):
        return False

    owns_client = client is None
    client = client or get_wp_client()
    try:
        if user.wp_user_id is not None:
            wp = await _update(user, client)
            action = "update"
        else:
            wp = await _create(user, client)
            action = "create"
        await identity.mark_wp_pushed(
            session,
            user,
            wp_user_id=int(wp["id"]),
            wp_modified_at=mapping.extract_modified(wp),
            synced_at=mapping.now_utc(),
        )
        await session.commit()
        # Audit (PII-scrubbed): identifiers only, never phone/email.
        log.info(
            "wp push ok: user_id=%s wp_user_id=%s action=%s",
            user.id,
            wp.get("id"),
            action,
        )
        return True
    except Exception:
        await session.rollback()
        raise
    finally:
        if owns_client:
            await client.aclose()


async def _create(user: User, client: WordPressClient) -> dict:
    username, email, password = mapping.synthesize_credentials(user)
    try:
        return await client.create_user(
            username=username,
            email=email,
            password=password,
            phone=user.phone,
            name=(user.full_name or None),
        )
    except WordPressUserExists as exc:
        # The phone/login/email is already in WP → link instead of duplicating.
        if exc.existing is None:
            raise WordPressError(
                f"WP user for user_id={user.id} exists but could not be resolved"
            ) from exc
        log.info("wp push: linking existing wp_user_id=%s", exc.existing.get("id"))
        return exc.existing


async def _update(user: User, client: WordPressClient) -> dict:
    fields: dict = {}
    if user.email:
        fields["email"] = user.email
    if user.full_name is not None:
        fields["name"] = user.full_name
    return await client.update_user(user.wp_user_id, fields=fields, phone=user.phone)


# ── pull (WP → app) ───────────────────────────────────────────────────────────
async def pull_users(
    session: AsyncSession,
    *,
    client: WordPressClient | None = None,
    mode: str | None = None,
) -> dict:
    """Paginate WP users and upsert each by phone (last-modified-wins).

    ``full`` (default) scans every page — the simplest thing that catches edits, made
    cheap by the idempotent upsert (it only writes when WP is actually newer).
    ``incremental`` walks newest-first and stops at the first already-known user
    (efficient new-user pickup; does not re-check edits to older users).
    """
    mode = mode or settings.wp_pull_mode
    per_page = settings.wp_pull_page_size
    order = "desc" if mode == "incremental" else "asc"
    owns_client = client is None
    client = client or get_wp_client()
    stats = {"pages": 0, "seen": 0, "applied": 0, "skipped": 0}
    page = 1
    try:
        while True:
            rows, total_pages = await client.list_users(
                page=page, per_page=per_page, order=order, orderby="registered"
            )
            if not rows:
                break
            stats["pages"] += 1
            stop = False
            for wp in rows:
                stats["seen"] += 1
                phone = mapping.extract_phone(wp)
                if phone is None:
                    stats["skipped"] += 1  # no usable join key → never guess, skip
                    continue
                wp_id = int(wp["id"]) if wp.get("id") is not None else None
                known = (
                    await identity.get_by_wp_user_id(session, wp_id)
                    if wp_id is not None
                    else None
                )
                fields = mapping.wp_to_local_fields(wp)
                _user, changed = await identity.upsert_from_wordpress(
                    session,
                    phone=phone,
                    wp_user_id=wp_id,
                    wp_modified_at=mapping.extract_modified(wp),
                    email=fields["email"],
                    full_name=fields["full_name"],
                )
                if changed:
                    stats["applied"] += 1
                if mode == "incremental" and known is not None:
                    stop = True  # everything older is already pulled
                    break
            await session.commit()
            if stop or page >= total_pages:
                break
            page += 1
        log.info("wp pull done: %s (mode=%s)", stats, mode)
        return stats
    except Exception:
        await session.rollback()
        raise
    finally:
        if owns_client:
            await client.aclose()


# ── reconcile (retry unfinished pushes) ───────────────────────────────────────
async def reconcile_pending(
    session: AsyncSession,
    *,
    client: WordPressClient | None = None,
    force: bool = False,
) -> dict:
    """Re-push local-origin users that aren't linked to WP yet (failed/never-run
    pushes). Idempotent: a successful push links ``wp_user_id`` so the user drops
    out of the pending set."""
    pending = await identity.list_pending_wp_push(session)
    owns_client = client is None
    client = client or get_wp_client()
    stats = {"pending": len(pending), "pushed": 0, "failed": 0}
    try:
        for user in pending:
            try:
                if await push_user(session, user.id, client=client, force=force):
                    stats["pushed"] += 1
            except WordPressError:
                stats["failed"] += 1
                log.warning("reconcile: push failed for user_id=%s (will retry)", user.id)
        log.info("wp reconcile done: %s", stats)
        return stats
    finally:
        if owns_client:
            await client.aclose()
