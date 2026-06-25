"""Shared FastAPI dependencies.

For this slice the only dependency is the DB session. Authentication, the
current-user dependency, and CSRF protection arrive in Phase 2 (sessions/OTP) —
there is no authenticated session to protect against CSRF yet.
"""

from __future__ import annotations

from app.db import get_session

# Stable import point for route modules.
get_db = get_session

# TODO(Phase 2): add `current_user` and CSRF dependencies once sessions exist.
