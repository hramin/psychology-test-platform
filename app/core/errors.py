"""Uniform application errors + handlers (deliberately simple)."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.templating import templates


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    status_code = 404


class ValidationError(AppError):
    status_code = 422


class Forbidden(AppError):
    status_code = 403


class RateLimited(AppError):
    status_code = 429


class AuthRequired(AppError):
    """Raised by ``login_required`` when there is no authenticated session.

    On the HTML surface this redirects to the login page (preserving ``next``);
    on the API surface it is a plain 401 JSON.
    """

    status_code = 401


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError):
        is_api = request.url.path.startswith("/api/")

        # HTML auth failures → bounce to the login page with a return path.
        if isinstance(exc, AuthRequired) and not is_api:
            nxt = quote(request.url.path)
            return RedirectResponse(f"/auth/login?next={nxt}", status_code=303)

        if is_api:
            return JSONResponse(
                {"detail": exc.message}, status_code=exc.status_code
            )
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": exc.message, "status_code": exc.status_code},
            status_code=exc.status_code,
        )
