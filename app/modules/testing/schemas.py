"""Pydantic schemas for the JSON API (``/api/v1``).

These describe the wire shapes for the JSON surface only — the HTML/HTMX flow
does not use them. Both surfaces call the same ``service.py`` functions, so the
business logic is defined exactly once.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field


class TestInfo(BaseModel):
    slug: str
    title: str
    answer_format: dict
    demographics: list[dict]
    pagination: dict
    scale_order: list[str]
    total_questions: int


class AttemptCreate(BaseModel):
    demographics: dict[str, Any] = Field(
        ...,
        description="Keys per the test's demographics spec, e.g. "
        '{"gender": "girl", "age": 15, "class": "نهم"}',
    )
    slug: str | None = Field(
        None, description="Test slug; defaults to the platform's active test."
    )


class AttemptState(BaseModel):
    id: uuid.UUID
    status: str  # in_progress | completed
    current_page: int
    total_pages: int
    answered: int
    total_questions: int
    demographics: dict[str, Any]


class AnswerOption(BaseModel):
    value: str
    label: str


class QuestionOut(BaseModel):
    id: int
    scale: str
    text: str
    selected: str | None = None


class QuestionsOut(BaseModel):
    page: int | None
    total_pages: int
    page_size: int
    answer_options: list[AnswerOption]
    questions: list[QuestionOut]


class AnswersIn(BaseModel):
    answers: dict[str, str] = Field(
        ...,
        description='Map of question id -> chosen option value, e.g. {"1": "yes", "2": "no"}',
    )


class ScaleScore(BaseModel):
    scale: str
    type: str  # validity | clinical
    raw: int
    mean: float
    t: float
    band: str | None = None  # "", "caution", or "severe"


class ResultOut(BaseModel):
    attempt_id: uuid.UUID
    status: str
    demographics: dict[str, Any]
    scale_order: list[str]
    scores: list[ScaleScore]
    t: dict[str, float]
    raw: dict[str, int]
    interpretation: dict
    chart: dict
