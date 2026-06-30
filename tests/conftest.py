"""Shared test fixtures.

The equivalence test must prove the JSON-driven engine reproduces the *original*
MMPI scoring. To keep the reference genuinely independent of ``mmpi_v1.json``, we
parse the frozen original artifact (``tests/fixtures/mmpi_original.html`` — a
verbatim copy of the verified ``mmpi.html``) and reconstruct its scoring from
that. If the JSON definition ever drifts (a wrong weight, a flipped reverse flag,
a mis-scaled item, a changed norm), the engine output diverges from this
reference and the test turns red — which CLAUDE.md treats as a release blocker.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFINITION_PATH = REPO_ROOT / "mmpi_v1.json"
FIXTURE_HTML = Path(__file__).resolve().parent / "fixtures" / "mmpi_original.html"


def _parse_original_html() -> dict:
    """Reconstruct the original questions + norms from the frozen HTML."""
    html = FIXTURE_HTML.read_text(encoding="utf-8")

    # question bank: id -> (scale, is_reverse)
    q_re = re.compile(
        r'\{\s*id:\s*(\d+),\s*scale:\s*"([^"]+)",\s*isReverse:\s*(true|false)'
    )
    questions = {
        int(qid): (scale, rev == "true") for qid, scale, rev in q_re.findall(html)
    }

    # norm table, split by gender block
    norm_block = re.search(
        r"const normTable\s*=\s*\{(.*?)\n\s*\};", html, re.DOTALL
    ).group(1)
    girl_region, boy_region = re.split(r"\bboy\s*:", norm_block, maxsplit=1)
    entry_re = re.compile(r"(\w+):\s*\{\s*mean:\s*([\d.]+),\s*sd:\s*([\d.]+)\s*\}")

    def parse(region: str) -> dict:
        return {
            scale: {"mean": float(mean), "sd": float(sd)}
            for scale, mean, sd in entry_re.findall(region)
        }

    norms = {"girl": parse(girl_region), "boy": parse(boy_region)}
    return {"questions": questions, "norms": norms}


def _original_score(reference: dict, responses: dict, gender: str):
    """Score exactly as the original ``calculateScores()`` did, including the
    JS ``Math.round((50 + 10*z) * 100) / 100`` rounding — implemented inline so
    this reference does not borrow the engine's ``js_round`` helper."""
    questions = reference["questions"]
    raw: dict[str, int] = {}
    for qid, (scale, is_reverse) in questions.items():
        is_yes = responses[qid] == "yes"
        score = (0 if is_yes else 1) if is_reverse else (1 if is_yes else 0)
        raw[scale] = raw.get(scale, 0) + score

    norms = reference["norms"][gender]
    t: dict[str, float] = {}
    for scale, n in norms.items():
        z = (raw[scale] - n["mean"]) / n["sd"]
        t[scale] = math.floor((50 + 10 * z) * 100 + 0.5) / 100
    return raw, t


@pytest.fixture(scope="session")
def definition() -> dict:
    return json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def reference() -> dict:
    parsed = _parse_original_html()

    def score(responses: dict, gender: str):
        return _original_score(parsed, responses, gender)

    return {**parsed, "score": score}


# ── Phase 2: identity / auth fixtures (no external services) ─────────────────
# fakeredis stands in for Redis; an in-memory SQLite engine (StaticPool → one
# shared connection) creates ONLY the identity tables (portable types), so no
# Postgres is needed; Celery runs eager so SMS sends inline via the mock backend.
import fakeredis.aioredis  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.modules.identity.models import OrgMember, Organization, User  # noqa: E402

_IDENTITY_TABLES = [User.__table__, Organization.__table__, OrgMember.__table__]


@pytest_asyncio.fixture
async def redis_fake():
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    try:
        yield r
    finally:
        await r.flushall()
        await r.aclose()


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=_IDENTITY_TABLES)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine):
    Session = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with Session() as session:
        yield session


@pytest.fixture
def fake_wp():
    """A fresh in-memory WordPress for the Phase-4 sync tests (no network)."""
    from app.modules.wpsync.client import FakeWordPressClient

    return FakeWordPressClient()


@pytest.fixture
def eager_sms(monkeypatch):
    """Run Celery tasks inline and use the mock SMS backend; yields its outbox."""
    from app.celery_app import celery_app
    from app.modules.notifications.sms import MockSmsBackend

    monkeypatch.setattr(settings, "sms_backend", "mock")
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    MockSmsBackend.outbox.clear()
    try:
        yield MockSmsBackend.outbox
    finally:
        celery_app.conf.task_always_eager = False


@pytest_asyncio.fixture
async def auth_client(db_engine, redis_fake, eager_sms):
    """httpx AsyncClient over the real ASGI app, with DB+Redis overridden. Uses
    ASGITransport so the app runs in the test's event loop (shares fakeredis +
    the SQLite engine)."""
    from httpx import ASGITransport, AsyncClient

    from app.deps import get_db, get_redis
    from app.main import app

    Session = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def _db():
        async with Session() as s:
            yield s

    async def _redis():
        yield redis_fake

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_redis] = _redis
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.clear()
