"""Typed application settings (pydantic-settings).

Every field has a dev default that matches the docker-compose service names, so
`docker compose up` works from a bare `.env` (copy of `.env.example`) and so the
module imports cleanly without any environment set. Secrets must always come
from the environment in real deployments — never commit a real `.env`.
"""

from __future__ import annotations

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

    # Catalog seeding
    definition_seed_path: str = "mmpi_v1.json"
    test_slug: str = "mmpi-teen-13"

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
