"""argon2 password hashing: round-trip, wrong password, malformed/empty hash."""

from __future__ import annotations

from app.modules.identity import passwords


def test_hash_then_verify_roundtrip():
    h = passwords.hash_password("Sup3rSecret!")
    assert h and h != "Sup3rSecret!"
    assert passwords.verify_password(h, "Sup3rSecret!") is True


def test_wrong_password_fails():
    h = passwords.hash_password("correct horse")
    assert passwords.verify_password(h, "battery staple") is False


def test_empty_or_malformed_hash_is_false():
    assert passwords.verify_password(None, "x") is False
    assert passwords.verify_password("", "x") is False
    assert passwords.verify_password("not-a-real-hash", "x") is False


def test_hashes_are_salted_unique():
    a = passwords.hash_password("same")
    b = passwords.hash_password("same")
    assert a != b  # random per-hash salt
    assert passwords.verify_password(a, "same")
    assert passwords.verify_password(b, "same")
