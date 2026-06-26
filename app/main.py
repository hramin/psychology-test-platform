"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.config import settings
from app.core.errors import register_error_handlers
from app.db import engine
from app.modules.identity.routes import router as identity_router
from app.modules.testing.api import router as api_v1_router
from app.modules.testing.routes import router as testing_router

# Import models so they are registered on Base.metadata (Alembic autogenerate /
# create_all see them).
from app.modules.catalog import models as _catalog_models  # noqa: F401
from app.modules.identity import models as _identity_models  # noqa: F401
from app.modules.testing import models as _testing_models  # noqa: F401

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Psychology Test Platform")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    register_error_handlers(app)
    app.include_router(identity_router)  # HTML / HTMX  (/auth)
    app.include_router(testing_router)  # HTML / HTMX
    app.include_router(api_v1_router)  # JSON  /api/v1

    @app.get("/health")
    async def health():
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "degraded"}, status_code=503)
        return {"status": "ok"}

    return app


app = create_app()
