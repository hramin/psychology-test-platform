#!/usr/bin/env bash
# Migrate (against direct Postgres) → seed the catalog → exec the server.
# Idempotent and resilient to the DB still coming up.
set -e

echo "[entrypoint] applying migrations..."
attempt=0
until alembic -c /srv/app/alembic.ini upgrade head; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 30 ]; then
    echo "[entrypoint] database not reachable after 30 attempts; aborting."
    exit 1
  fi
  echo "[entrypoint] migration attempt $attempt failed; retrying in 2s..."
  sleep 2
done

echo "[entrypoint] seeding catalog from ${DEFINITION_SEED_PATH:-mmpi_v1.json}..."
python -m app.modules.catalog.seed || echo "[entrypoint] WARNING: seed step failed (continuing)"

# Provision the bootstrap admin (idempotent; no-op unless SEED_ADMIN_PHONE is set).
echo "[entrypoint] seeding admin (if SEED_ADMIN_PHONE set)..."
python -m app.manage seed-admin || echo "[entrypoint] WARNING: seed-admin failed (continuing)"

echo "[entrypoint] starting app: $*"
exec "$@"
