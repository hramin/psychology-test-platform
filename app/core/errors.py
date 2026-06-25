"""Uniform application errors + handlers (deliberately simple)."""

from __future__ import annotations

from fastapi import FastAPI, Request

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


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"message": exc.message, "status_code": exc.status_code},
            status_code=exc.status_code,
        )
