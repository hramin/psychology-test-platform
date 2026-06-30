"""Typed application settings (pydantic-settings).

Every field has a dev default that matches the docker-compose service names, so
`docker compose up` works from a bare `.env` (copy of `.env.example`) and so the
module imports cleanly without any environment set. Secrets must always come
from the environment in real deployments — never commit a real `.env`.
"""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "dev"
    secret_key: str = "dev-insecure-secret-change-me"

    # Direct Postgres connection (asyncpg). Used by Alembic migrations and as the
    # fallback for the app engine.
    database_url: str = "postgresql+asyncpg://psych:psych@postgres:5432/psych"
    # Through PgBouncer (transaction mode) — the app runtime path. Falls back to
    # database_url when empty.
    pgbouncer_url: str = "postgresql+asyncpg://psych:psych@pgbouncer:6432/psych"

    redis_url: str = "redis://redis:6379/0"

    # CORS for the JSON API (/api/v1) — set CORS_ORIGINS to a comma-separated
    # list of allowed origins for a separately-hosted frontend. Default "*"
    # (fine while the API is unauthenticated; tighten once auth/cookies land).
    cors_origins: list[str] = ["*"]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    # Catalog seeding
    definition_seed_path: str = "mmpi_v1.json"
    test_slug: str = "mmpi-teen-13"

    # ── Sessions & cookies (Phase 2) ─────────────────────────────────────────
    session_cookie_name: str = "pst_session"
    csrf_cookie_name: str = "pst_csrf"
    session_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    # Secure cookies require HTTPS; off by default so dev over http://localhost
    # works. MUST be true in any real (TLS) deployment — set COOKIE_SECURE=true.
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    # ── OTP (Phase 2) ────────────────────────────────────────────────────────
    otp_ttl_seconds: int = 120
    otp_code_length: int = 5
    otp_max_attempts: int = 5  # wrong tries before the code is burned
    # Hard rate limits (per phone + per IP), counted per purpose ('login'/'reset').
    otp_phone_cooldown_seconds: int = 60       # ≥ 1 SMS / 60s per phone
    otp_phone_hourly_max: int = 5              # ≤ 5 SMS / hour per phone
    otp_ip_hourly_max: int = 20                # per-IP cap / hour
    # Unknown phone on successful verify → create a source='local' user (signup
    # == login). Set false to reject phones with no existing user.
    otp_auto_create_users: bool = True

    # ── Passwords (Phase 3) ──────────────────────────────────────────────────
    password_min_length: int = 8
    # Brute-force guard on username/password login — counters are separate from
    # the OTP limits and only tick on a *failed* attempt (per identifier + IP).
    password_login_window_seconds: int = 900   # 15 min sliding-ish fixed window
    password_login_subject_max: int = 10       # failed tries / window / identifier
    password_login_ip_max: int = 50            # failed tries / window / source IP

    # ── SMS / Kavenegar (Phase 2) ────────────────────────────────────────────
    sms_backend: str = "mock"  # 'mock' (logs the code) | 'kavenegar'
    kavenegar_api_key: str = ""
    kavenegar_sender: str = ""
    kavenegar_use_template: bool = False       # true → verify_lookup (OTP template)
    kavenegar_otp_template: str = ""
    otp_message_template: str = "کد ورود شما: {code}"

    # ── Celery (Phase 2) ─────────────────────────────────────────────────────
    # Default broker/result to the Redis URL; override per-env if desired.
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_always_eager: bool = False     # tests set this true (run inline)

    # ── Admin bootstrap (Phase 2) ────────────────────────────────────────────
    # The first admin user, provisioned from the environment (never hardcoded).
    seed_admin_phone: str = ""
    seed_admin_username: str = ""

    # ── WordPress bidirectional sync (Phase 4) ───────────────────────────────
    # BOTH directions go through the WP REST API (never MySQL). All sync work runs
    # in Celery workers, off the request path. Disabled by default and pointed at
    # an in-memory fake client so dev/test/CI never touch the network — set
    # WP_SYNC_ENABLED=true + WP_CLIENT_BACKEND=http + the WP_* creds to go live.
    wp_sync_enabled: bool = False
    wp_client_backend: str = "fake"            # 'fake' (in-memory) | 'http' (real)
    wp_rest_base: str = ""                      # e.g. https://example.com/wp-json
    wp_api_user: str = ""                       # least-privilege WP account login
    wp_api_app_password: str = ""               # Application Password — a SECRET
    # Usermeta keys exposed to REST by the must-use plugin (wordpress/mu-plugins).
    wp_phone_meta_key: str = "billing_phone"    # the join key
    wp_modified_meta_key: str = "pst_modified_gmt"  # last-modified-wins timestamp
    # Read sync (WP → app).
    wp_pull_mode: str = "full"                  # 'full' (catches edits) | 'incremental'
    wp_pull_page_size: int = 100                # REST per_page
    wp_sync_interval_minutes: int = 10          # Beat cadence for the pull
    wp_reconcile_interval_minutes: int = 15     # Beat cadence for the push-retry
    # Write sync (app → WP).
    # 'synthesize' → derive username/email from phone for phone-only signups so the
    # WP create always succeeds; 'defer' → push only once a real email exists;
    # 'manual' → never auto-push (admin "sync now" only).
    wp_push_policy: str = "synthesize"
    wp_default_role: str = "subscriber"         # role assigned to pushed users
    wp_placeholder_email_domain: str = "users.invalid"  # synthesized-email domain
    wp_http_timeout_seconds: float = 15.0

    # Garage / S3 — inert until Phase 6 (reports).
    garage_endpoint: str = ""
    garage_key: str = ""
    garage_secret: str = ""
    garage_bucket: str = "reports"

    @property
    def app_database_url(self) -> str:
        """The URL the running app uses (PgBouncer when configured)."""
        return self.pgbouncer_url or self.database_url


settings = Settings()
