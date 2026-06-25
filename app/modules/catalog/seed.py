"""Idempotent seeder: load the test definition JSON into the catalog.

Run standalone:  ``python -m app.modules.catalog.seed``
It is also invoked by the container entrypoint on startup, so a single
``docker compose up`` produces a ready-to-use catalog. Safe to re-run.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal
from app.modules.catalog import service
from app.modules.catalog.models import Test, TestVersion


async def seed_from_file(path: str | Path | None = None) -> tuple[str, int]:
    path = Path(path or settings.definition_seed_path)
    definition = json.loads(path.read_text(encoding="utf-8"))
    slug = definition["slug"]
    version = int(definition["version"])
    title = definition["title"]

    async with SessionLocal() as session:
        async with session.begin():
            test = (
                await session.execute(select(Test).where(Test.slug == slug))
            ).scalar_one_or_none()
            if test is None:
                test = Test(slug=slug, title=title, is_active=True)
                session.add(test)
                await session.flush()
            else:
                test.title = title
                test.is_active = True

            tv = (
                await session.execute(
                    select(TestVersion).where(
                        TestVersion.test_id == test.id,
                        TestVersion.version == version,
                    )
                )
            ).scalar_one_or_none()
            if tv is None:
                session.add(
                    TestVersion(
                        test_id=test.id,
                        version=version,
                        definition=definition,
                        is_active=True,
                    )
                )
            else:
                tv.definition = definition
                tv.is_active = True

    service.invalidate(slug)
    return slug, version


def main() -> None:
    slug, version = asyncio.run(seed_from_file())
    print(f"seeded {slug} v{version}")


if __name__ == "__main__":
    main()
