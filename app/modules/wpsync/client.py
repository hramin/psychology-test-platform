"""WordPress REST client — the only channel to WordPress (read AND write).

Both sync directions go through the WP REST API over HTTPS; there is **no MySQL
path**. The client is hidden behind the ``WordPressClient`` Protocol (mirroring
``notifications/sms.py``) so the whole sync is exercisable against an in-memory
fake — tests and dev never touch the network. Auth is HTTP Basic with a
least-privilege WP account + Application Password (a secret, env-only).
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from app.config import settings
from app.modules.wpsync.mapping import now_utc

log = logging.getLogger("wpsync.client")

_USERS = "/wp/v2/users"


class WordPressError(RuntimeError):
    """A WP REST call failed (network/HTTP 5xx/auth). Triggers retry/reconcile."""


class WordPressUserExists(Exception):
    """WP rejected a *create* because the login or email already exists.

    Carries the resolved existing user dict when we could look it up (so the caller
    links ``wp_user_id`` and switches to an update), else ``None``.
    """

    def __init__(self, message: str, existing: dict | None = None) -> None:
        super().__init__(message)
        self.existing = existing


class WordPressClient(Protocol):
    async def list_users(
        self,
        *,
        page: int,
        per_page: int,
        order: str = "asc",
        orderby: str = "registered",
    ) -> tuple[list[dict], int]:
        """Return ``(users, total_pages)`` for one page (``context=edit``)."""
        ...

    async def get_user(self, wp_user_id: int) -> dict | None: ...

    async def find_user_by_phone(self, phone: str) -> dict | None: ...

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        phone: str,
        name: str | None = None,
        roles: list[str] | None = None,
    ) -> dict: ...

    async def update_user(
        self, wp_user_id: int, *, fields: dict, phone: str | None = None
    ) -> dict: ...

    async def aclose(self) -> None: ...


# ── real client (httpx + Application Password Basic auth) ─────────────────────
class HttpWordPressClient:
    """Talks to a live WordPress over the REST API. Reads use ``context=edit`` so
    the registered usermeta (phone + modified timestamp) and email come back."""

    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        app_password: str | None = None,
    ) -> None:
        base = (base_url or settings.wp_rest_base).rstrip("/")
        if not base:
            raise WordPressError("WP_REST_BASE is not configured.")
        # Application Passwords contain spaces in the UI; WP accepts them with or
        # without, but strip to be safe for the Basic header.
        pw = (app_password or settings.wp_api_app_password or "").replace(" ", "")
        self._client = httpx.AsyncClient(
            base_url=base,
            auth=(user or settings.wp_api_user, pw),
            timeout=settings.wp_http_timeout_seconds,
            headers={"Accept": "application/json"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _meta_payload(self, phone: str | None) -> dict:
        return {settings.wp_phone_meta_key: phone} if phone else {}

    async def list_users(
        self,
        *,
        page: int,
        per_page: int,
        order: str = "asc",
        orderby: str = "registered",
    ) -> tuple[list[dict], int]:
        try:
            resp = await self._client.get(
                _USERS,
                params={
                    "context": "edit",
                    "page": page,
                    "per_page": per_page,
                    "order": order,
                    "orderby": orderby,
                },
            )
        except httpx.HTTPError as exc:  # network/timeout
            raise WordPressError(f"WP list_users failed: {exc}") from exc
        # A page past the end returns 400 (rest_post_invalid_page_number) — treat as empty.
        if resp.status_code == 400 and b"invalid_page_number" in resp.content:
            return [], page - 1
        if resp.status_code >= 400:
            raise WordPressError(
                f"WP list_users HTTP {resp.status_code}: {resp.text[:300]}"
            )
        total_pages = int(resp.headers.get("X-WP-TotalPages", "1") or "1")
        return resp.json(), total_pages

    async def get_user(self, wp_user_id: int) -> dict | None:
        try:
            resp = await self._client.get(
                f"{_USERS}/{wp_user_id}", params={"context": "edit"}
            )
        except httpx.HTTPError as exc:
            raise WordPressError(f"WP get_user failed: {exc}") from exc
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise WordPressError(f"WP get_user HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json()

    async def find_user_by_phone(self, phone: str) -> dict | None:
        """Best-effort lookup by the join key. WP core ``search`` matches the login
        (we synthesize username == phone) but not arbitrary meta, so we also confirm
        the phone usermeta when present."""
        try:
            resp = await self._client.get(
                _USERS,
                params={"context": "edit", "search": phone, "per_page": 20},
            )
        except httpx.HTTPError as exc:
            raise WordPressError(f"WP find_user_by_phone failed: {exc}") from exc
        if resp.status_code >= 400:
            return None
        for u in resp.json():
            meta = u.get("meta") or {}
            mp = meta.get(settings.wp_phone_meta_key)
            if isinstance(mp, list):
                mp = mp[0] if mp else None
            if str(mp or "") == phone or str(u.get("username") or "") == phone:
                return u
        return None

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        phone: str,
        name: str | None = None,
        roles: list[str] | None = None,
    ) -> dict:
        payload: dict = {
            "username": username,
            "email": email,
            "password": password,
            "roles": roles or [settings.wp_default_role],
            "meta": self._meta_payload(phone),
        }
        if name:
            payload["name"] = name
        try:
            resp = await self._client.post(_USERS, json=payload)
        except httpx.HTTPError as exc:
            raise WordPressError(f"WP create_user failed: {exc}") from exc
        if resp.status_code in (200, 201):
            return resp.json()
        # Duplicate login/email → let the caller link the existing account.
        code = _error_code(resp)
        if resp.status_code == 400 and code in (
            "existing_user_login",
            "existing_user_email",
        ):
            existing = await self.find_user_by_phone(phone)
            raise WordPressUserExists(f"WP user already exists ({code})", existing)
        raise WordPressError(f"WP create_user HTTP {resp.status_code}: {resp.text[:300]}")

    async def update_user(
        self, wp_user_id: int, *, fields: dict, phone: str | None = None
    ) -> dict:
        payload = dict(fields)
        meta = self._meta_payload(phone)
        if meta:
            payload["meta"] = {**payload.get("meta", {}), **meta}
        try:
            resp = await self._client.post(f"{_USERS}/{wp_user_id}", json=payload)
        except httpx.HTTPError as exc:
            raise WordPressError(f"WP update_user failed: {exc}") from exc
        if resp.status_code >= 400:
            raise WordPressError(
                f"WP update_user HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()


def _error_code(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("code", ""))
    except Exception:  # noqa: BLE001 — non-JSON error body
        return ""


# ── in-memory fake (tests + dev) ──────────────────────────────────────────────
class FakeWordPressClient:
    """A complete in-memory WordPress, same Protocol as the real client.

    Lets the full bidirectional sync run with zero network. ``seed`` injects a user
    as if created in WP; ``edit`` simulates a WP-side change (bumping the modified
    timestamp). ``create_count`` / ``update_count`` make idempotency assertions easy.
    """

    def __init__(self) -> None:
        self.store: dict[int, dict] = {}
        self._next_id = 1
        self.create_count = 0
        self.update_count = 0

    async def aclose(self) -> None:  # symmetry with the real client
        return None

    # -- test/dev helpers (not part of the Protocol) --
    def seed(
        self,
        *,
        phone: str,
        email: str | None = None,
        name: str | None = None,
        username: str | None = None,
        modified: str | None = None,
    ) -> dict:
        uid = self._next_id
        self._next_id += 1
        rec = {
            "id": uid,
            "username": username or phone,
            "email": email or f"{phone}@example.test",
            "name": name or "",
            "roles": [settings.wp_default_role],
            "registered_date": now_utc().isoformat(),
            "meta": {
                settings.wp_phone_meta_key: phone,
                settings.wp_modified_meta_key: modified or now_utc().isoformat(),
            },
        }
        self.store[uid] = rec
        return _copy(rec)

    def edit(self, wp_user_id: int, *, modified: str | None = None, **fields) -> dict:
        rec = self.store[wp_user_id]
        meta = fields.pop("meta", None)
        rec.update(fields)
        if meta:
            rec["meta"].update(meta)
        rec["meta"][settings.wp_modified_meta_key] = modified or now_utc().isoformat()
        return _copy(rec)

    # -- Protocol --
    async def list_users(
        self,
        *,
        page: int,
        per_page: int,
        order: str = "asc",
        orderby: str = "registered",
    ) -> tuple[list[dict], int]:
        rows = sorted(self.store.values(), key=lambda r: r["id"], reverse=order == "desc")
        total = len(rows)
        total_pages = max(1, (total + per_page - 1) // per_page)
        start = (page - 1) * per_page
        return [_copy(r) for r in rows[start : start + per_page]], total_pages

    async def get_user(self, wp_user_id: int) -> dict | None:
        rec = self.store.get(wp_user_id)
        return _copy(rec) if rec else None

    async def find_user_by_phone(self, phone: str) -> dict | None:
        for rec in self.store.values():
            if str(rec.get("meta", {}).get(settings.wp_phone_meta_key) or "") == phone:
                return _copy(rec)
        return None

    async def create_user(
        self,
        *,
        username: str,
        email: str,
        password: str,
        phone: str,
        name: str | None = None,
        roles: list[str] | None = None,
    ) -> dict:
        for rec in self.store.values():
            if str(rec.get("username")) == username or str(rec.get("email")) == email:
                raise WordPressUserExists("exists", await self.find_user_by_phone(phone))
        self.create_count += 1
        return self.seed(
            phone=phone, email=email, name=name or "", username=username
        )

    async def update_user(
        self, wp_user_id: int, *, fields: dict, phone: str | None = None
    ) -> dict:
        if wp_user_id not in self.store:
            raise WordPressError(f"no such WP user {wp_user_id}")
        self.update_count += 1
        meta = dict(fields.get("meta") or {})
        if phone:
            meta[settings.wp_phone_meta_key] = phone
        clean = {k: v for k, v in fields.items() if k != "meta"}
        return self.edit(wp_user_id, meta=meta or None, **clean)


def _copy(rec: dict) -> dict:
    out = dict(rec)
    out["meta"] = dict(rec.get("meta") or {})
    return out


# ── factory ───────────────────────────────────────────────────────────────────
_fake_singleton: FakeWordPressClient | None = None


def get_wp_client() -> WordPressClient:
    """Real HTTP client when configured for it, else a process-wide fake.

    Service functions accept an explicit ``client`` (tests inject their own fake);
    this factory is the default for the Celery tasks and the management CLI.
    """
    if settings.wp_client_backend == "http":
        return HttpWordPressClient()
    global _fake_singleton
    if _fake_singleton is None:
        _fake_singleton = FakeWordPressClient()
    return _fake_singleton
