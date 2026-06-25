"""HTMX test-flow routes: start → paginated answering → scored result."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import ValidationError
from app.core.templating import templates
from app.deps import get_db
from app.modules.catalog import service as catalog
from app.modules.testing import service

router = APIRouter()


def _hx_redirect(url: str) -> Response:
    """Tell HTMX to do a full client-side navigation (refresh-safe URLs)."""
    return Response(status_code=204, headers={"HX-Redirect": url})


def _page_context(attempt, definition: dict, page: int, error: str | None = None) -> dict:
    page = service.clamp_page(definition, page)
    questions = [
        {"id": q["id"], "text": q["text"], "selected": (attempt.responses or {}).get(str(q["id"]))}
        for q in service.questions_for_page(definition, page)
    ]
    total = service.total_pages(definition)
    return {
        "attempt": attempt,
        "title": definition["title"],
        "page": page,
        "total": total,
        "questions": questions,
        "answer_options": service.answer_options(definition),
        "pct": round((page + 1) / total * 100),
        "is_first": page == 0,
        "is_last": page == total - 1,
        "error": error,
    }


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_db)):
    active = await catalog.get_active(session, settings.test_slug)
    if active is None:
        # catalog not seeded yet
        return templates.TemplateResponse(
            request,
            "error.html",
            {"status_code": 404, "message": "هیچ آزمون فعالی یافت نشد."},
            status_code=404,
        )
    _, definition = active
    return templates.TemplateResponse(
        request,
        "start.html",
        {
            "title": definition["title"],
            "demographics": definition["demographics"],
            "error": None,
            "values": {},
        },
    )


@router.post("/attempt")
async def create_attempt(request: Request, session: AsyncSession = Depends(get_db)):
    form = await request.form()
    raw = {k: v for k, v in form.items()}
    active = await catalog.get_active(session, settings.test_slug)
    definition = active[1] if active else {"demographics": [], "title": ""}
    try:
        attempt = await service.start_attempt(session, settings.test_slug, raw)
        await session.commit()
    except ValidationError as exc:
        # Re-render the start form (HTMX swaps #start-area) keeping entered values.
        return templates.TemplateResponse(
            request,
            "partials/start_form.html",
            {
                "title": definition["title"],
                "demographics": definition["demographics"],
                "error": exc.message,
                "values": raw,
            },
            status_code=200,
        )
    return _hx_redirect(f"/attempt/{attempt.id}")


@router.get("/attempt/{attempt_id}", response_class=HTMLResponse)
async def view_attempt(
    attempt_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db)
):
    attempt = await service.get_attempt(session, attempt_id)
    if attempt.status == "completed":
        return RedirectResponse(f"/attempt/{attempt_id}/result", status_code=303)
    definition = await service.definition_for(session, attempt)
    ctx = _page_context(attempt, definition, attempt.current_page)
    return templates.TemplateResponse(request, "attempt.html", ctx)


@router.post("/attempt/{attempt_id}/page/{page}", response_class=HTMLResponse)
async def submit_page(
    attempt_id: uuid.UUID,
    page: int,
    request: Request,
    session: AsyncSession = Depends(get_db),
):
    attempt = await service.get_attempt(session, attempt_id)
    if attempt.status == "completed":
        return _hx_redirect(f"/attempt/{attempt_id}/result")

    definition = await service.definition_for(session, attempt)
    form = await request.form()
    direction = form.get("dir", "next")
    answers = {k[2:]: v for k, v in form.items() if k.startswith("q-")}

    missing = await service.record_answers(attempt, definition, page, answers)

    # Going forward (next/finish) requires a complete page; stay put with an error.
    if direction in ("next", "finish") and missing:
        attempt.current_page = service.clamp_page(definition, page)
        await session.commit()
        ctx = _page_context(
            attempt, definition, page,
            error="لطفاً پیش از ادامه، به همهٔ سؤالات این صفحه پاسخ دهید.",
        )
        return templates.TemplateResponse(request, "partials/test_page.html", ctx)

    if direction == "finish":
        await service.finish_attempt(session, attempt)
        await session.commit()
        return _hx_redirect(f"/attempt/{attempt_id}/result")

    target = page - 1 if direction == "prev" else page + 1
    target = service.clamp_page(definition, target)
    attempt.current_page = target
    await session.commit()
    ctx = _page_context(attempt, definition, target)
    return templates.TemplateResponse(request, "partials/test_page.html", ctx)


@router.get("/attempt/{attempt_id}/result", response_class=HTMLResponse)
async def view_result(
    attempt_id: uuid.UUID, request: Request, session: AsyncSession = Depends(get_db)
):
    attempt = await service.get_attempt(session, attempt_id)
    if attempt.status != "completed":
        return RedirectResponse(f"/attempt/{attempt_id}", status_code=303)
    result = await service.get_result(session, attempt)
    # TODO(Phase 8): audit-log this result/report access.
    chart = service.chart_payload(result)
    return templates.TemplateResponse(
        request, "result.html", {"chart": chart, **result}
    )
