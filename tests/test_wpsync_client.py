"""FakeWordPressClient + mapping helpers (Phase 4) — no DB, no network."""

from __future__ import annotations

import pytest

from app.config import settings
from app.modules.wpsync import mapping
from app.modules.wpsync.client import FakeWordPressClient, WordPressUserExists


async def test_fake_create_and_get():
    wp = FakeWordPressClient()
    rec = await wp.create_user(
        username="09120000000", email="a@b.test", password="x", phone="09120000000"
    )
    assert rec["id"] and wp.create_count == 1
    got = await wp.get_user(rec["id"])
    assert got["meta"][settings.wp_phone_meta_key] == "09120000000"


async def test_fake_duplicate_raises_with_existing():
    wp = FakeWordPressClient()
    await wp.create_user(username="u1", email="a@b.test", password="x", phone="09120000001")
    with pytest.raises(WordPressUserExists) as exc:
        await wp.create_user(
            username="u1", email="other@b.test", password="x", phone="09120000001"
        )
    assert exc.value.existing is not None
    assert exc.value.existing["meta"][settings.wp_phone_meta_key] == "09120000001"
    assert wp.create_count == 1  # the collision did not create a second user


async def test_fake_list_users_paginates():
    wp = FakeWordPressClient()
    for i in range(5):
        await wp.create_user(
            username=f"u{i}", email=f"u{i}@b.test", password="x", phone=f"0912000000{i}"
        )
    page1, total = await wp.list_users(page=1, per_page=2)
    assert len(page1) == 2 and total == 3
    page3, _ = await wp.list_users(page=3, per_page=2)
    assert len(page3) == 1


async def test_fake_find_by_phone():
    wp = FakeWordPressClient()
    wp.seed(phone="09121110000", email="x@y.test")
    found = await wp.find_user_by_phone("09121110000")
    assert found["meta"][settings.wp_phone_meta_key] == "09121110000"
    assert await wp.find_user_by_phone("09120000000") is None


def test_extract_phone_normalizes():
    rec = {"meta": {settings.wp_phone_meta_key: "+98 912 345 6789"}}
    assert mapping.extract_phone(rec) == "09123456789"
    rec_fa = {"meta": {settings.wp_phone_meta_key: "۰۹۱۲۳۴۵۶۷۸۹"}}
    assert mapping.extract_phone(rec_fa) == "09123456789"
    assert mapping.extract_phone({"meta": {}}) is None
    assert mapping.extract_phone({"meta": {settings.wp_phone_meta_key: "nope"}}) is None


def test_extract_modified_parses_and_falls_back():
    rec = {"meta": {settings.wp_modified_meta_key: "2026-06-30T10:00:00+00:00"}}
    dt = mapping.extract_modified(rec)
    assert dt is not None and dt.year == 2026 and dt.tzinfo is not None
    rec2 = {"meta": {}, "registered_date": "2026-01-01T00:00:00"}
    assert mapping.extract_modified(rec2).year == 2026  # core fallback, made tz-aware
    assert mapping.extract_modified({"meta": {}}) is None


def test_synthesize_credentials(monkeypatch):
    monkeypatch.setattr(settings, "wp_placeholder_email_domain", "users.invalid")

    class PhoneOnly:
        phone, username, email, full_name = "09120001122", None, None, None

    username, email, pw = mapping.synthesize_credentials(PhoneOnly())
    assert username == "09120001122"
    assert email == "09120001122@users.invalid"
    assert pw  # non-empty random password

    class Complete:
        phone, username, email, full_name = "09120001122", "ali", "ali@real.test", "Ali"

    u, e, _ = mapping.synthesize_credentials(Complete())
    assert u == "ali" and e == "ali@real.test"


def test_can_push_policies(monkeypatch):
    class PhoneOnly:
        phone, username, email = "0912", None, None

    class WithEmail:
        phone, username, email = "0912", None, "a@b.test"

    monkeypatch.setattr(settings, "wp_push_policy", "synthesize")
    assert mapping.can_push(PhoneOnly()) is True
    monkeypatch.setattr(settings, "wp_push_policy", "manual")
    assert mapping.can_push(PhoneOnly()) is False
    monkeypatch.setattr(settings, "wp_push_policy", "defer")
    assert mapping.can_push(PhoneOnly()) is False   # defers until a real email exists
    assert mapping.can_push(WithEmail()) is True
