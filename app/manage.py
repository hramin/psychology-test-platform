"""Tiny management CLI.

Usage:
    python -m app.manage seed-admin
    python -m app.manage wp-pull     # WordPress → app (read sync), one shot
    python -m app.manage wp-push     # app → WordPress (flush pending), one shot

``seed-admin`` provisions the bootstrap admin from the environment
(``SEED_ADMIN_PHONE`` + optional ``SEED_ADMIN_USERNAME``) — the number is never
hardcoded. It is **idempotent**: re-running only ensures ``is_admin=true`` for that
phone and never creates a duplicate (unique ``phone`` constraint). No-op with a
clear message when ``SEED_ADMIN_PHONE`` is unset.

``wp-pull`` / ``wp-push`` run one WordPress sync pass on demand (the same code Beat
runs), for ops/debugging. They use the configured client (``WP_CLIENT_BACKEND``);
``wp-push`` forces the push policy since it is an explicit operator action.
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


async def _wp_pull() -> int:
    from app.modules.wpsync import service as wpsync

    async with SessionLocal() as session:
        stats = await wpsync.pull_users(session)
    print(f"[wp-pull] {stats}")
    return 0


async def _wp_push() -> int:
    from app.modules.wpsync import service as wpsync

    async with SessionLocal() as session:
        stats = await wpsync.reconcile_pending(session, force=True)
    print(f"[wp-push] {stats}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.manage")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed-admin", help="Provision the bootstrap admin from env.")
    sub.add_parser("wp-pull", help="Run one WordPress→app read sync.")
    sub.add_parser("wp-push", help="Flush pending app→WordPress pushes (forced).")
    args = parser.parse_args(argv)

    if args.command == "seed-admin":
        return asyncio.run(_seed_admin())
    if args.command == "wp-pull":
        return asyncio.run(_wp_pull())
    if args.command == "wp-push":
        return asyncio.run(_wp_push())
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
