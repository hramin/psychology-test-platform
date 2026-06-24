# Psychology Test Platform — CLAUDE.md

Persistent project context. Read this first, then `ARCHITECTURE_AND_PLAN.md` for depth.
`mmpi_v1.json` is the verified MMPI test definition — its scoring is already proven
correct against the original; **never change the scoring math**.

---

## What this is
A web platform for delivering psychological tests (first test: MMPI-Teen-13, a 130-item
adolescent instrument in Persian), with individual and organization billing, SMS/OTP
login, WordPress identity sync, and beautiful PDF reports. Small-to-medium scale.

## Stack (fixed — do not substitute)
- Backend: **FastAPI**, fully async (asyncpg, async SQLAlchemy, httpx).
- Frontend: **HTMX + Jinja2** server-rendered HTML. **No SPA. No GraphQL.**
- Tasks: **Celery + Redis**.
- Data: **PostgreSQL** (via **PgBouncer** transaction mode) + **Garage** (S3-compatible) for files.
- Deploy: **Docker Compose + Nginx**. One app image; web and workers differ only by command.

---

## THE most important architectural rule
This is a **modular monolith with separate worker processes — NOT network microservices.**
If you ever consider splitting into independently deployed, network-separated services
with their own databases: don't. It breaks wallet atomicity (turns it into a distributed
transaction) and adds ops cost this scale doesn't justify. Instead:

- One FastAPI app, internally split into modules under `app/modules/`.
- **Modules communicate only through each other's `service.py` functions.**
- **A module never reads or writes another module's tables directly.**
- That discipline keeps the seams clean so a module *could* be extracted later if ever needed.

## Performance principle (the 200 req/s guarantee)
- The web tier only does fast Postgres/Redis work and **enqueues** Celery tasks.
- Every slow third party runs in a worker: **SMS send, PDF render, future LLM**.
- **The single exception** is ZarinPal *verify* — called inline, because the user is
  mid-redirect waiting for it and it's a quick server-to-server round-trip.
- Never put a blocking call (sync DB driver, `requests`, CPU-heavy scoring loop on huge
  inputs) inside an async handler — it stalls the event loop.
- Cache test definitions / questions / norms in Redis (they rarely change → hot path
  skips Postgres). App tier is **stateless** → scale by adding `web` replicas.
- Watch **Celery queue depth** as the backpressure signal for workers.

---

## Domain model — entitlement is the unifying primitive
The right to take one attempt of one test version is an **entitlement**. Both billing
models just *produce* one; the test engine only ever asks "does this user hold a valid,
unused entitlement for this test?" — it never knows how it was paid for.

- **Individual** pays per exam → payment produces one entitlement.
- **Organization** charges a prepaid wallet → allocating to a named user (by phone)
  produces one entitlement, debited from the wallet.
- **Individuals never hold a balance.** **Only organizations get a wallet** (prepaid
  balance + append-only ledger). This asymmetry is intentional.

### Entitlement state machine (robustness lives here, not in the charging mechanism)
`available → reserved` (attempt started) → `consumed` (attempt completed + scored).
`reserved → available` if abandoned/timed-out (a Beat sweep releases stale ones).
`available → refunded` on refund / org revoke.
A crash mid-exam just releases the seat back to `available` — no refund, no re-charge.

### Wallet atomicity (money-critical — get this exactly right)
- Debit is a **single conditional SQL statement**:
  `UPDATE wallets SET balance_cents = balance_cents - :cost WHERE id=:id AND balance_cents >= :cost RETURNING ...`
  plus the **ledger row inserted in the SAME transaction**.
- **Never** read-then-write the balance in Python.
- The ledger is **append-only**; balance must always equal `SUM(delta_cents)`.

### Org allocation
Assigned model (allocate test → specific user by phone), debit at allocation time,
revoke unused → credit back. A "pool" variant (N credits, any invited user consumes)
can be added later on the same primitive.

---

## Multi-test from day one
Even with one live test, model a `tests` catalog + `test_versions` (versioned JSONB
definitions + norms). **Results pin to a `test_version`** so an old result stays
reproducible after the instrument is revised. **Adding a new test = inserting rows,
never changing engine code.**

## The generic scoring engine
- Each question option carries a **weight**; a scale's raw score = sum of chosen weights.
  This one idea generalizes 2/4/6-choice single-select tests. MMPI binary is
  `{yes:1, no:0}` (flipped for reverse items) — reproduces the original exactly.
- T-score: `z = (raw - mean) / sd`; `T = round(50 + 10*z, 2)` using **JS `Math.round`
  semantics** (round half toward +inf) via a `js_round` helper — not Python's banker's rounding.
- The norm group is selected by a demographic field declared in the definition
  (`gender` for MMPI: girl/boy). **Demographics are captured at attempt start**, because
  WordPress only provides phone/email/username — not gender.
- **Single-choice only. No multi-select.**

## MMPI specifics (from mmpi_v1.json — preserve exactly)
- 130 items, 13 scales × 10: validity `L F K`; clinical `Hs D Hy Pa Sc Ma Pd Si PK A`.
- 11 reverse-scored items: 40, 53, 66, 79, 92, 124, 125, 126, 127, 128, 130.
- Gender-specific norms. Validity flag `T > 60`; clinical caution `T ≥ 65`, severe `T ≥ 70`.
- Paginated 10 questions/page. Chart: line, y-axis 20–90, reference lines at T=50/65/70,
  vertical divider between K and Hs, validity vs clinical as two series.
- **An equivalence test must verify the engine matches the original scoring and stay green
  in CI. Treat a red equivalence test as a release blocker.**

## Interpretation is a seam (for future AI)
- Stored in an `interpretations` row with `source` = `'rule'` now.
- v1 is rule-based (thresholds from the definition).
- Later, an AI worker writes the **same row shape** with `source='ai'`. Build the seam,
  leave it empty — **adding AI must require zero schema or web-tier change.**

---

## Frontend
- Server-rendered HTML partials swapped by HTMX. Primary wire format is **HTML**, not JSON.
- A tiny JSON/HTML-partial surface only for: attempt-status polling, report-status polling,
  and the ZarinPal callback/webhook.
- **Test progress is stored server-side on the attempt** → refresh-safe, device-portable,
  app stays stateless.
- Chart.js for the result profile, faithful to the MMPI chart described above.

## Design system
- Brand color `#00a379`. Derive tints/shades from it; see tokens in the plan doc.
- **Severity colors encode the T-score bands** (ok = normal, caution = T≥65, severe = T≥70)
  — they are meaning, not decoration.
- Font **Vazirmatn**. Direction **RTL** (Persian). Clinical, minimal, generous whitespace.
- Every page has a footer and a logo slot (`{% block logo %}`).

## Reports
- HTML/CSS template → PDF via **Playwright/Chromium** (real CSS, correct RTL, embedded
  Vazirmatn). Store in Garage; serve via short-lived signed URLs.
- Runs in an **isolated `reports` worker** (Chromium is RAM-heavy → low concurrency).

## Integrations
- **Kavenegar (SMS/OTP):** thin wrapper, send **always** via the `notifications` worker.
  OTP code hashed in Redis with ~120s TTL; verify against Redis. **Hard rate limits**:
  e.g. 1 SMS/60s and 5/hour per phone, plus a per-IP cap.
- **ZarinPal (payments):** request → redirect → **server-to-server verify (inline)**.
  Never trust the redirect. Callback must be **idempotent** (unique `idempotency_key`,
  status check). Reconcile pending payments via a Beat job.
- **WordPress sync:** **read-only** MySQL, **off the request path**. A Beat job upserts
  into Postgres **keyed on phone**, incrementally via a stored high-water mark. Manual
  trigger available from the admin panel. **Only phone, email, username.** One-way (WP→app).

---

## Security (health data about MINORS — treat with high care)
- Encrypt sensitive columns at rest: responses, raw/T scores, interpretations.
- Audit-log every access to a result/report.
- Sessions in Redis (HTTP-only, Secure, SameSite cookies); CSRF on every mutating form.
- AuthZ enforced in `service.py`, not templates: a user sees only their own attempts/results;
  an org owner only their org; admin gated.
- Secrets only via `.env` / environment, never committed, never baked into images.
  WordPress credentials are read-only. Scrub PII from logs.
- Future AI interpretation must explain scores, never make diagnostic claims.

## Config & conventions
- `pydantic-settings` → one typed `Settings` from `.env`. Provide `.env.example`.
- Keep error handling reliable but simple — uniform error types/handlers, not elaborate.
- Clean, readable, not over-engineered. Prefer clarity over cleverness.

---

## Build phases (each must run before the next)
0 Skeleton · 1 Catalog+Engine · 2 Identity/OTP · 3 WP sync · 4 Test flow ·
5 Billing · 6 Reports · 7 Admin · 8 Hardening · later AI.
**Start with 0 → 1 → 4** (entitlement check stubbed "always allow"): a runnable, scoreable
MMPI with no auth or payments. Wire the real entitlement gate in during Phase 5.

## Workflow rules for Claude Code
- For any non-trivial phase: research the relevant plan section, propose a file-by-file
  plan, and **wait for approval before writing code**.
- Run `pytest` before declaring a phase done. Keep the equivalence test green.
- One phase at a time; `/clear` between phases.

## Commands
- Run:   `docker compose up --build`
- Test:  `docker compose run --rm web pytest`
- Migrate: `docker compose run --rm web alembic upgrade head`
