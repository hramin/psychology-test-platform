# Psychology Test Platform — MMPI-Teen-13

A web platform for delivering psychological tests. This repository currently implements the
first runnable slice — **Phases 0, 1, and 4** of the build plan — a working MMPI-Teen-13 test
a user can click through end-to-end that produces a correctly-scored T-score profile with a
Chart.js chart and rule-based interpretation.

> **No auth, no payments, no external APIs in this slice.** The entitlement check is stubbed
> "always allow" with a clearly-marked `TODO(Phase 5)` at the exact seam in
> `app/modules/testing/service.py`.

See `CLAUDE.md` and `ARCHITECTURE_AND_PLAN.md` for the full design.

## Stack

FastAPI (async) · HTMX + Jinja2 (server-rendered, no SPA) · PostgreSQL (via PgBouncer
transaction mode) + async SQLAlchemy/asyncpg · Alembic · Redis & Garage (present for later
phases) · Docker Compose + Nginx.

## Quick start

```bash
docker compose up --build
```

The web container's entrypoint **automatically applies migrations and seeds the catalog**
(`mmpi_v1.json`) on startup, so this single command is enough. Then open:

```
http://localhost          # nginx → web
```

`GET /health` returns `{"status":"ok"}` once the DB is reachable.

### Click through the test

1. On the start page, fill in **جنسیت (gender — required)**, **سن (age 10–20)**, and
   **پایه تحصیلی (class)**, then submit.
2. Answer the **130 items across 13 pages** (بله/خیر), 10 per page. Each page is validated
   before you can advance; **«صفحهٔ قبل»** goes back. Progress is saved server-side on every
   page — refresh or switch devices and you resume exactly where you were.
3. On the last page press **«اتمام و محاسبهٔ پروفایل»**.
4. The result page shows the **T-score profile chart** (y-axis 20–90, reference lines at
   T=50/65/70, a divider between the validity scales `K` and the clinical scales `Hs`, and
   validity vs. clinical as two series), a numeric raw/mean/T table, and short interpretation
   blocks colored by severity band (ok / caution `T≥65` / severe `T≥70`).

## Tests

```bash
docker compose run --rm web pytest
```

The suite is pure-Python (no DB needed) and includes the **equivalence test**
(`tests/test_equivalence.py`) — a **release blocker** that scores 5,000 random respondents
through both the engine and an independent reference reconstructed from the frozen original
`tests/fixtures/mmpi_original.html`, asserting identical raw and T-scores. A red equivalence
test blocks release. **Never change the scoring math.**

## Manual ops (normally automatic via the entrypoint)

```bash
docker compose run --rm web alembic -c app/alembic.ini upgrade head      # migrate
docker compose run --rm web python -m app.modules.catalog.seed           # seed catalog
```

## JSON API (`/api/v1`)

A reusable JSON surface runs **alongside** the HTML/HTMX app for separately-hosted
clients (SPA, mobile, other services). It's a thin adapter over the same
`service.py` — no business logic is duplicated, so the two surfaces never drift.
Interactive docs at **`/docs`**; set `CORS_ORIGINS` (comma-separated) to your frontend's
origin(s).

| Method & path | Purpose |
|---|---|
| `GET  /api/v1/tests/{slug}` | Test metadata (title, demographics spec, answer options, scale order) |
| `POST /api/v1/attempts` | Start an attempt — body `{ "demographics": {gender, age, class}, "slug"? }` → `201` + state |
| `GET  /api/v1/attempts/{id}` | Attempt state (status, current_page, answered/total) |
| `GET  /api/v1/attempts/{id}/questions[?page=N]` | Questions (+ saved answers); all, or one page |
| `PATCH /api/v1/attempts/{id}/answers` | Merge answers — body `{ "answers": {"1":"yes", ...} }` |
| `POST /api/v1/attempts/{id}/finish` | Score + interpret → result (`422` if incomplete) |
| `GET  /api/v1/attempts/{id}/result` | Scores, T-values, interpretation, and chart payload |

Errors are JSON (`{"detail": "..."}`, `422`/`404`); the HTML pages still render error pages.

```bash
# minimal flow
curl -X POST localhost/api/v1/attempts -H 'content-type: application/json' \
  -d '{"demographics":{"gender":"girl","age":15,"class":"نهم"}}'
curl -X PATCH localhost/api/v1/attempts/<id>/answers -H 'content-type: application/json' \
  -d '{"answers":{"1":"no","2":"no"}}'      # …answer all 130
curl -X POST localhost/api/v1/attempts/<id>/finish
```

## Scoring Engine service (`engine/`)

A **standalone, stateless** FastAPI service that loads **instrument plugins** and exposes a scoring API.
It has **no database and no dependency on the app** — it's the one service extracted from the modular
monolith (see `ARCHITECTURE_AND_PLAN.md` §0). MMPI is the first plugin (byte-for-byte faithful to the
original); a tiny `wellbeing-8` Likert plugin proves the architecture supports a totally different
calculation + result path. Full contract and a "how to add an instrument" guide live in
[`engine/ENGINE.md`](engine/ENGINE.md).

It runs as its own Compose service, reachable only on the internal network (no host port, Nginx never
proxies to it). The app will call it via `EngineClient` in Phase 5.

```bash
docker compose up engine                 # serve (internal); GET /healthz inside the network
docker compose run --rm engine pytest    # unit + API + MMPI equivalence (release blocker), all green
```

| Method & path | Purpose |
|---|---|
| `GET  /healthz` | liveness + plugin count |
| `GET  /instruments` | `[{slug, version, title, kind}]` |
| `GET  /instruments/{slug}/schema?version=` | InstrumentMeta + QuestionSchema |
| `POST /score` | `{slug, version?, responses, demographics}` → `{score_result, result_view}` |

## WordPress bidirectional sync (Phase 4)

Keeps the app's `users` and a WordPress user base in sync **both ways over the WordPress
REST API only — there is no MySQL access**. **Phone is the join key**, conflicts resolve
**last-modified-wins**, and all network I/O runs in Celery workers (off the request path).
Lives in `app/modules/wpsync/` and mirrors the `notifications` module's shape; like every
module it touches only its own concern — all `users` reads/writes go through
`identity.service`, never the table directly.

**Off by default.** `WP_SYNC_ENABLED=false` and `WP_CLIENT_BACKEND=fake` (an in-memory
WordPress), so dev/test/CI never touch the network. Configure the `WP_*` block in
[`.env.example`](.env.example) and set `WP_SYNC_ENABLED=true` + `WP_CLIENT_BACKEND=http`
to go live.

### WordPress-side setup (one-time)
1. Copy [`wordpress/mu-plugins/pst-sync.php`](wordpress/mu-plugins/pst-sync.php) into the
   site's `wp-content/mu-plugins/`. It exposes the phone usermeta (`billing_phone`, the join
   key) and a server-managed modified timestamp (`pst_modified_gmt`) to the REST API.
2. Create a dedicated **least-privilege** account (e.g. `sync-bot`) with the caps
   `list_users`/`create_users`/`edit_users` (Administrator, or a custom role) and generate an
   **Application Password** for it. That password is a **secret** — set it only via the
   environment (`WP_API_APP_PASSWORD`), never commit it.

### How it works
- **READ (WP → app):** Beat runs a paginated `GET /wp/v2/users?context=edit` every
  `WP_SYNC_INTERVAL_MINUTES`, upserts each row by phone, and skips rows without a phone. Never
  deletes. Default `WP_PULL_MODE=full` scans every page (the simplest thing that catches edits;
  made cheap by an idempotent upsert that only writes when WP is genuinely newer). `incremental`
  walks newest-first and stops at the first already-known user (efficient new-user pickup).
- **WRITE (app → WP):** a new local signup (or a local profile edit) enqueues a push after the
  DB commit → `POST /wp/v2/users`, storing the returned WP id; updates use
  `POST /wp/v2/users/{id}`. A Beat **reconciler** retries pushes that never linked. With the
  default `WP_PUSH_POLICY=synthesize`, phone-only signups get a phone-derived username + a
  placeholder email so the create always succeeds (`defer` / `manual` are the alternatives).
- **Loop-prevention:** the pull path **never enqueues a push** and **`source='wp'` rows are
  never pushed** — so a record from one side cannot be bounced back to the other.
  **Idempotency:** inbound is last-modified-wins (re-pull = no-op); outbound is create-or-update
  keyed on `wp_user_id`, and a create that collides with an existing WP user **links** it instead
  of duplicating (re-push = no-op).

### Run it / trigger it
```bash
docker compose up beat worker-wpsync      # Beat schedules the pull + reconcile; the worker runs them
```
An admin can force either direction now from **`/admin/wpsync`** (CSRF-protected buttons), or
from the CLI:
```bash
docker compose run --rm web python -m app.manage wp-pull   # WP → app, one pass
docker compose run --rm web python -m app.manage wp-push   # flush pending app → WP (forced)
```

## Project layout

```
app/
  config.py · db.py · deps.py · main.py        # skeleton: settings, async DB, app factory, /health
  core/        templating.py · errors.py
  modules/
    catalog/   models · service · seed          # tests + versioned definitions (loads mmpi_v1.json)
    testing/   models · service · routes
               engine/scoring.py                # generic, MMPI-exact (js_round, compute_scores)
               engine/interpret.py              # rule-based (the AI seam, source='rule')
    identity/  models · service · routes         # users/orgs, phone/OTP + password auth, sessions
    notifications/ service · tasks · sms         # Celery `notifications` queue (OTP SMS)
    wpsync/    client · mapping · service · tasks · routes   # Phase 4 WordPress REST sync
  templates/ · static/ (tokens.css + vendored htmx & chart.js)
  alembic/                                       # migrations 0001–0004
tests/                                           # equivalence + scoring + interpret + identity + wpsync
wordpress/mu-plugins/pst-sync.php                # WP-side: exposes phone + modified meta to REST
nginx/ · docker-compose.yml · garage.toml · mmpi_v1.json
```

### Architectural notes

- **Modular monolith.** Modules talk only through each other's `service.py`. `testing` reads
  catalog data via `catalog.service` and never touches catalog tables directly. The scoring
  engine is pure (operates on the definition dict).
- **Generic scoring.** Each option carries a `weight`; a scale's raw score is the sum of
  chosen weights — so 2/4/6-choice tests work unchanged. MMPI binary is `{yes:1,no:0}`
  (flipped for the 11 reverse items).
- **Refresh-safe.** Attempt progress (responses + current page) lives on the attempt row in
  Postgres, so the app tier is stateless.

### Deliberately deferred (each marked with a `TODO` in code)

- Real entitlement gate (reserve→consume) — **Phase 5**.
- Auth / identity / OTP / sessions / CSRF — **Phase 2**.
- Column encryption + audit logging — **Phase 8**.
- Redis caching, Celery workers, Garage usage, PDF reports — **Phases 5/6**.
