# Psychology Test Platform — CLAUDE.md

Read this first, then `ARCHITECTURE_AND_PLAN.md` for depth. `mmpi_v1.json` holds the verified
MMPI data; its scoring is proven correct against the original `mmpi.html` — **never change the
scoring math.**

---

## What this is
A platform for delivering psychological tests. Two product surfaces share one **scoring engine**:
(1) an end-user web app (HTMX) with accounts, billing, and reports; (2) a **B2B API** that lets
other businesses call the scoring engine under contract. A separate React UI is planned and must
be supported by the same APIs. First instrument: MMPI (130-item adolescent, Persian).

## Stack (fixed)
- Backend: **FastAPI**, fully async (asyncpg, async SQLAlchemy, httpx).
- Frontend: **HTMX + Jinja2** server-rendered HTML. No SPA in the app itself (React is a separate
  future client of the API).
- Tasks: **Celery + Redis**. Data: **PostgreSQL** (via **PgBouncer**) + **Garage** (S3) for files.
- Deploy: **Docker Compose + Nginx**.

---

## THE architecture rule (read carefully — it changed)
The system is a **modular monolith for the transactional core** PLUS **exactly one extracted
stateless service: the Scoring Engine.**

- **Monolith ("app"):** identity/auth, billing/wallet, attempts & results storage, B2B API
  management, admin. Modules talk only through each other's `service.py`; no module touches
  another's tables.
- **Scoring Engine (separate service):** pure, stateless computation. Given an instrument +
  responses + demographics, it returns scores and a result view. It is extracted **because** it is
  stateless, shares no DB transaction with anything, and has multiple consumers (the app, B2B
  clients, future React).
- **Do NOT extract anything else into a service** — above all not billing/wallet, where atomicity
  lives. The engine **never** reads the app's database. The app calls the engine over HTTP behind
  an `EngineClient` interface (so it could even run in-proc, but we deploy it as a service).

## The Scoring Engine = instrument plugins (this is the core abstraction)
Each test is a **plugin**: its own code module implementing ONE uniform `Instrument` interface,
with **arbitrarily different internals**. This is what supports MBTI (type), NEO (facet→domain),
Gardner (ranked strengths), Strong (interest themes) — logic that no config blob could express.

```python
class Instrument(Protocol):
    slug: str; version: int
    def metadata(self) -> InstrumentMeta: ...        # title, demographics, page_size
    def question_schema(self) -> QuestionSchema: ...  # items + options (2/4/6 or forced-choice)
    def score(self, responses, demographics) -> ScoreResult: ...   # ← arbitrary per-test logic
    def build_result(self, score) -> ResultView: ...  # generic render model (see below)
```

- Plugins are **version-controlled, unit-tested code**, discovered by a registry at startup.
- The unifying contract is the **output**: a generic `ResultView`
  (`kind: profile|type|index|themes`, items with value/band/severity, interpretation blocks, and a
  chart spec `type: line|bar|radar|gauge`). One ResultView → one generic renderer for HTMX, React,
  and the PDF report. No per-test view code.
- **Adding a test = writing one isolated plugin** (+ its tests). Nothing else in the system changes.
- **MMPI is the first plugin** and must reproduce `mmpi.html` exactly (see below). The engine ships
  an equivalence test (5000 random respondents, plugin vs original logic, zero divergence) that is
  a **release blocker** if it ever goes red.

## MMPI specifics (preserve exactly — from mmpi_v1.json)
130 items, 13 scales×10: validity `L F K`; clinical `Hs D Hy Pa Sc Ma Pd Si PK A`. Reverse items:
40,53,66,79,92,124,125,126,127,128,130. Gender norms. T = round(50+10·z, 2) with JS `Math.round`
(half-up) via `js_round`. Validity flag T>60; clinical caution T≥65, severe T≥70. Paginated 10/page.
Chart: line, y 20–90, reference lines 50/65/70, divider between K and Hs, validity vs clinical
series. ResultView: kind="profile", chart type="line". Demographics (gender required → norm group,
age, class) captured at attempt start.

---

## App ↔ Engine integration
- Catalog `test_versions` reference an `instrument_slug` + `instrument_version` (the engine plugin)
  plus commerce fields (price). The app fetches the schema from the engine to render, collects
  responses via HTMX, calls the engine to score, and **stores the returned ScoreResult + ResultView**
  (the app owns attempts/responses/results; the engine stores nothing).
- All engine calls go through `EngineClient` (httpx) behind an interface; tests mock it.

## Authentication (app users)
- **Two first-class login methods: username/password AND phone+OTP.** Plus **OTP-based password
  recovery.**
- **Every login path ends at one `establish_session(user)` seam** (mints the Redis session). OTP
  login, password login, recovery, and a future OIDC callback all funnel through it.
- Reserve `users.external_idp` + `users.external_sub` (nullable) now for future Keycloak.
- OTP codes hashed in Redis, ~120s TTL, **purpose-tagged** ('login' vs 'reset' — never cross-usable).
  Hard rate limits per phone + per IP, counted per purpose. No account enumeration on recovery.
- Sessions in Redis; HTTP-only/Secure/SameSite cookies; CSRF on every mutating form. AuthZ in
  `service.py`, not templates.

## B2B API management (separate bounded context)
For other businesses to call the scoring engine under contract. **Completely separate from user
wallet/purchasing/accounting** — a B2B client is not a user; a quota is not wallet credit.
- **Dedicated tables:** `api_clients`, `api_keys` (public key id + **hashed** secret, status,
  `expires_at`), and per-key **independently configurable** limits: rate limit, request quota,
  quota period. Plus `api_usage` for metering/audit.
- **Managed via an admin JSON API** (not the HTMX panel for now): issue/revoke keys (secret shown
  once), set/adjust limits and expiry independently, manage contracts (duration + quota).
- **Public B2B API** (`/api/v1/...`) authenticates by key+secret, enforces expiry + rate limit +
  quota, meters usage, then proxies to the engine. The engine stays internal-only and stateless.
- These tables and the admin API are isolated from user/billing modules and from the HTMX user UI.

## Billing & pricing (monolith — unchanged core)
Entitlement is the unifying primitive (individual payment OR org-wallet allocation both produce one;
state machine available→reserved→consumed, reserved→available on timeout). Wallet debit is a SINGLE
conditional SQL statement + append-only ledger row in the SAME transaction; never read-then-write;
balance == SUM(delta). Individuals never hold a balance; only orgs have wallets. **Each test carries
its own price** in the catalog (per test_version), admin-editable, read by billing — pricing a test
needs no code.

## WordPress sync (bidirectional, off the request path)
Use the **WordPress REST API for BOTH directions** (read and write) over HTTPS — **no direct MySQL
connection**. READ lists/pulls users via REST; WRITE creates/updates users via REST (never write
`wp_users`/`wp_usermeta` directly). The phone usermeta must be exposed to REST via a small must-use
plugin (`show_in_rest`). Phone is the join key; last-modified-wins; no deletes; idempotent;
loop-prevented. Caveat: the core users endpoint has no clean "modified since" filter, so detect edits
via a periodic full paginated scan or a small WP-side timestamp helper (see the guide in
PHASE_PROMPTS.md).

## Reports & AI
Reports: HTML→PDF via Playwright/Chromium (RTL, Vazirmatn), rendered **generically from ResultView**,
stored in Garage, served via signed URLs, in an isolated `reports` worker. AI interpretation is a
seam: `interpretations.source` is 'rule' now; later an `ai` worker writes 'ai' to the same row with
zero schema/web change. Guardrail: AI explains, never diagnoses; output appropriate for adolescents.

## Security (health data about MINORS)
Encrypt sensitive columns at rest (responses, scores, interpretations); audit-log result/report
access, password resets, WP writes, and B2B API calls; secrets only via env/Infisical; WP DB creds
read-only; API secrets stored hashed; PII scrubbed from logs.

---

## Build order (phases — details in PHASE_PROMPTS.md)
Done: 0 skeleton, 1 catalog + verified MMPI logic.
**Next, in order:**
**A. Scoring Engine Service + MMPI plugin (DO FIRST, test standalone)** → 2 Identity + OTP →
3 Username/password login + OTP recovery → 4 WP bidirectional sync → 5 Test flow (consumes engine) →
6 Billing + pricing → 7 Reports → 8 B2B API accounts & keys (admin API) → 9 Public B2B scoring API →
10 Admin panel (HTMX) → 11 Hardening → AI → **Keycloak OIDC (last)**.

## Workflow rules for Claude Code
Investigate the repo + read the plan section, propose a file-by-file plan, WAIT for approval, then
build. Run `pytest` before declaring a phase done; keep the MMPI equivalence test green. One phase
at a time; `/clear` between phases. Commit per phase.

## Commands
Run: `docker compose up --build` · Test app: `docker compose run --rm app pytest` ·
Test engine: `docker compose run --rm engine pytest` · Migrate: `... alembic upgrade head`
