"""Tiny management CLI.

Usage:
    python -m app.manage seed-admin

``seed-admin`` provisions the bootstrap admin from the environment
(``SEED_ADMIN_PHONE`` + optional ``SEED_ADMIN_USERNAME``) — the number is never
hardcoded. It is **idempotent**: re-running only ensures ``is_admin=true`` for that
phone and never creates a duplicate (unique ``phone`` constraint). No-op with a
clear message when ``SEED_ADMIN_PHONE`` is unset.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings
from app.db import SessionLocal
from app.modules.identity import service as identity


async def _seed_admin() -> int:
    phone = settings.seed_admin_phone.strip()
    if not phone:
        print("[seed-admin] SEED_ADMIN_PHONE not set; nothing to do.")
        return 0
    username = settings.seed_admin_username.strip() or None
    async with SessionLocal() as session:
        user = await identity.upsert_admin(session, phone, username)
        await session.commit()
        print(
            f"[seed-admin] admin ready: id={user.id} phone={user.phone} "
            f"username={user.username or '-'} is_admin={user.is_admin}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed-admin", help="Provision the bootstrap admin from env.")
    args = parser.parse_args(argv)

    if args.command == "seed-admin":
        return asyncio.run(_seed_admin())
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
