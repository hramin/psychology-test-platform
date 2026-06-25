"""JSON API (``/api/v1``) — a reusable surface for non-HTML clients (SPA, mobile,
other services).

This router is a *thin* adapter: it only translates JSON <-> the shared
``service.py`` functions that the HTML/HTMX routes also use. No business logic
lives here, so the two surfaces can never drift. Errors raised by the service
(NotFoundError / ValidationError) are rendered as JSON by the central handler in
``app.core.errors`` for any path under ``/api/``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.errors import NotFoundError, ValidationError
from app.deps import get_db
from app.modules.catalog import service as catalog
from app.modules.testing import schemas
from app.modules.testing import service

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


# ── helpers ─────────────────────────────────────────────────────────────────
async def _load(session: AsyncSession, attempt_id: uuid.UUID):
    attempt = await service.get_attempt(session, attempt_id)
    definition = await service.definition_for(session, attempt)
    return attempt, definition


def _state(attempt, definition) -> schemas.AttemptState:
    responses = attempt.responses or {}
    return schemas.AttemptState(
        id=attempt.id,
        status=attempt.status,
        current_page=attempt.current_page,
        total_pages=service.total_pages(definition),
        answered=len(responses),
        total_questions=len(definition["questions"]),
        demographics=attempt.demographics,
    )


async def _result(session, attempt) -> schemas.ResultOut:
    data = await service.get_result(session, attempt)
    definition = data["definition"]
    type_by_scale = {s["key"]: s["type"] for s in definition["scales"]}
    scores = [
        schemas.ScaleScore(
            scale=row["scale"],
            type=type_by_scale[row["scale"]],
            raw=row["raw"],
            mean=row["mean"],
            t=row["t"],
            band=row["band"] or None,
        )
        for row in data["table"]
    ]
    return schemas.ResultOut(
        attempt_id=attempt.id,
        status=attempt.status,
        demographics=data["demographics"],
        scale_order=data["scale_order"],
        scores=scores,
        t=data["t"],
        raw=data["raw"],
        interpretation=data["interpretation"],
        chart=service.chart_payload(data),
    )


# ── catalog ─────────────────────────────────────────────────────────────────
@router.get("/tests/{slug}", response_model=schemas.TestInfo)
async def get_test(slug: str, session: AsyncSession = Depends(get_db)):
    """Everything a client needs to render the form and pages for a test."""
    active = await catalog.get_active(session, slug)
    if active is None:
        raise NotFoundError("آزمون فعالی با این شناسه یافت نشد.")
    _, d = active
    return schemas.TestInfo(
        slug=d["slug"],
        title=d["title"],
        answer_format=d["answer_format"],
        demographics=d["demographics"],
        pagination=d.get("pagination", {}),
        scale_order=d["scale_order"],
        total_questions=len(d["questions"]),
    )


# ── attempt lifecycle ───────────────────────────────────────────────────────
@router.post(
    "/attempts",
    response_model=schemas.AttemptState,
    status_code=status.HTTP_201_CREATED,
)
async def create_attempt(
    body: schemas.AttemptCreate,
    response: Response,
    session: AsyncSession = Depends(get_db),
):
    slug = body.slug or settings.test_slug
    attempt = await service.start_attempt(session, slug, body.demographics)
    await session.commit()
    definition = await service.definition_for(session, attempt)
    response.headers["Location"] = f"/api/v1/attempts/{attempt.id}"
    return _state(attempt, definition)


@router.get("/attempts/{attempt_id}", response_model=schemas.AttemptState)
async def get_attempt(attempt_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    attempt, definition = await _load(session, attempt_id)
    return _state(attempt, definition)


@router.get("/attempts/{attempt_id}/questions", response_model=schemas.QuestionsOut)
async def get_questions(
    attempt_id: uuid.UUID,
    page: int | None = None,
    session: AsyncSession = Depends(get_db),
):
    """All questions (default) or a single page (``?page=N``), with saved answers."""
    attempt, definition = await _load(session, attempt_id)
    responses = attempt.responses or {}
    if page is None:
        qs = definition["questions"]
    else:
        page = service.clamp_page(definition, page)
        qs = service.questions_for_page(definition, page)
    questions = [
        schemas.QuestionOut(
            id=q["id"],
            scale=q["scale"],
            text=q["text"],
            selected=responses.get(str(q["id"])),
        )
        for q in qs
    ]
    return schemas.QuestionsOut(
        page=page,
        total_pages=service.total_pages(definition),
        page_size=service.page_size(definition),
        answer_options=[
            schemas.AnswerOption(**o) for o in service.answer_options(definition)
        ],
        questions=questions,
    )


@router.patch("/attempts/{attempt_id}/answers", response_model=schemas.AttemptState)
async def save_answers(
    attempt_id: uuid.UUID,
    body: schemas.AnswersIn,
    session: AsyncSession = Depends(get_db),
):
    """Merge a set of answers (by question id). Idempotent; safe to call repeatedly."""
    attempt, definition = await _load(session, attempt_id)
    if attempt.status == "completed":
        raise ValidationError("این آزمون قبلاً تکمیل شده است.")
    await service.set_answers(attempt, definition, body.answers)
    await session.commit()
    return _state(attempt, definition)


@router.post("/attempts/{attempt_id}/finish", response_model=schemas.ResultOut)
async def finish_attempt(
    attempt_id: uuid.UUID, session: AsyncSession = Depends(get_db)
):
    """Score + interpret. 422 if not all questions are answered yet."""
    attempt, _ = await _load(session, attempt_id)
    await service.finish_attempt(session, attempt)
    await session.commit()
    return await _result(session, attempt)


@router.get("/attempts/{attempt_id}/result", response_model=schemas.ResultOut)
async def get_result(attempt_id: uuid.UUID, session: AsyncSession = Depends(get_db)):
    attempt, _ = await _load(session, attempt_id)
    if attempt.status != "completed":
        raise ValidationError("نتیجه هنوز آماده نیست؛ ابتدا آزمون را تکمیل کنید.")
    return await _result(session, attempt)
