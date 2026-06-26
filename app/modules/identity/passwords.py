"""Password hashing (argon2id) for username/password auth.

The single place the hashing algorithm and its parameters are chosen.
``users.password_hash`` stores argon2's encoded string (algorithm + params + salt
+ digest), so a future parameter bump or algorithm swap is transparent to
verification of already-stored hashes.

``verify_password`` never raises: a wrong password, a malformed/empty hash, or a
``NULL`` hash all return ``False``. ``DUMMY_HASH`` lets a "user not found" path
spend the same CPU as a genuine verify, so response timing can't be used to
enumerate accounts.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_ph = PasswordHasher()


def hash_password(plain: str) -> str:
    """Return the argon2id encoded hash for ``plain``."""
    return _ph.hash(plain)


def verify_password(stored_hash: str | None, plain: str) -> bool:
    """True iff ``plain`` matches ``stored_hash``. Never raises.

    A missing/empty hash (e.g. an OTP-only user who never set a password) or a
    malformed stored value both verify as ``False``."""
    if not stored_hash:
        return False
    try:
        return _ph.verify(stored_hash, plain)
    except (VerificationError, InvalidHashError):
        return False


# A real argon2 hash to verify against on the "no such user / no password" path,
# so it costs the same as a genuine check (constant-time-ish anti-enumeration).
DUMMY_HASH = _ph.hash("not-a-real-password")
