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
  templates/ · static/ (tokens.css + vendored htmx & chart.js)
  alembic/                                       # initial migration
tests/                                           # equivalence + scoring + interpret + definition
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
