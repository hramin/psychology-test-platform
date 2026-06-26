"""The engine's public contracts — the *only* shared vocabulary between the
generic core (API, registry, renderer) and the arbitrarily-different instrument
plugins.

Two halves:

1. **Instrument I/O** — what a plugin advertises (``InstrumentMeta`` +
   ``QuestionSchema``) and what its ``score()`` returns (``ScoreResult``).
2. **The unifying output** — ``ResultView`` (+ ``ResultItem`` / ``ChartSpec`` /
   ``InterpretationBlock``). Every instrument, no matter how different its
   internals, emits this one shape so a single renderer (HTMX, React, PDF) can
   draw any test with no per-test view code.

These are Pydantic v2 models so FastAPI serialises and validates them for free.
Pydantic is *not* a web dependency, so plugins stay pure Python.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── instrument I/O ───────────────────────────────────────────────────────────
class OptionSpec(BaseModel):
    """One selectable answer. ``weight`` is optional in the wire schema (clients
    rendering the form don't need it); plugins keep weights internally."""

    value: str
    label: str
    weight: float | int | None = None


class QuestionItem(BaseModel):
    id: int
    text: str
    scale: str | None = None  # forced-choice / index tests may omit this
    options: list[OptionSpec]


class DemographicField(BaseModel):
    key: str
    label: str
    type: str  # "choice" | "int" | "text"
    required: bool = False
    options: list[OptionSpec] | None = None
    min: int | None = None
    max: int | None = None
    drives_norm_group: bool = False


class InstrumentMeta(BaseModel):
    slug: str
    title: str
    version: int
    kind: str  # ResultView kind this instrument produces: profile|type|index|themes
    demographics: list[DemographicField] = Field(default_factory=list)
    page_size: int | None = None
    answer_format: dict | None = None  # shared option set, when uniform across items


class QuestionSchema(BaseModel):
    items: list[QuestionItem]
    answer_format: dict | None = None
    pagination: dict | None = None


class ScoreResult(BaseModel):
    """Instrument-specific numbers. ``raw`` holds the primitive sums; ``derived``
    holds whatever the instrument computes from them (T-scores, an index, a type
    code, ranked themes …). The shape inside is the plugin's business."""

    raw: dict[str, Any] = Field(default_factory=dict)
    derived: dict[str, Any] = Field(default_factory=dict)


# ── the unifying output: ResultView ──────────────────────────────────────────
class ResultItem(BaseModel):
    key: str
    label: str
    value: float | int | str
    band: str | None = None       # e.g. "" | "caution" | "severe" | "low" | "high"
    severity: str | None = None   # semantic severity for colour: ok|caution|severe|flag
    extra: dict[str, Any] = Field(default_factory=dict)  # raw, mean, type, …


class InterpretationBlock(BaseModel):
    title: str
    body: str
    severity: str | None = None
    section: str | None = None  # optional grouping, e.g. "validity" / "clinical"


class ReferenceLine(BaseModel):
    value: float
    kind: str               # baseline|caution|severe|threshold …
    label: str | None = None


class ChartSeries(BaseModel):
    name: str
    data: list[float | int | None]


class ChartDivider(BaseModel):
    index: int              # drawn between item (index-1) and item (index)
    left_label: str | None = None
    right_label: str | None = None


class ChartSpec(BaseModel):
    type: str               # line | bar | radar | gauge
    labels: list[str] = Field(default_factory=list)
    series: list[ChartSeries] = Field(default_factory=list)
    y_min: float | None = None
    y_max: float | None = None
    reference_lines: list[ReferenceLine] = Field(default_factory=list)
    dividers: list[ChartDivider] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)  # gauge value/min/max, etc.


class ResultView(BaseModel):
    kind: str  # profile | type | index | themes
    summary: str
    items: list[ResultItem] = Field(default_factory=list)
    interpretation: list[InterpretationBlock] = Field(default_factory=list)
    chart: ChartSpec | None = None
    meta: dict[str, Any] = Field(default_factory=dict)  # slug, version, scored_at, …


# ── API request/response envelopes ───────────────────────────────────────────
class InstrumentSummary(BaseModel):
    slug: str
    version: int
    title: str
    kind: str


class SchemaOut(BaseModel):
    meta: InstrumentMeta
    schema_: QuestionSchema = Field(serialization_alias="schema", alias="schema")
    model_config = {"populate_by_name": True}


class ScoreRequest(BaseModel):
    slug: str
    version: int | None = None  # defaults to the latest registered version
    responses: dict[str, Any] = Field(
        default_factory=dict,
        description='Map of question id -> chosen option value, e.g. {"1": "yes"}',
    )
    demographics: dict[str, Any] = Field(default_factory=dict)


class ScoreResponse(BaseModel):
    slug: str
    version: int
    score_result: ScoreResult
    result_view: ResultView


# ── uniform error type ───────────────────────────────────────────────────────
class EngineError(Exception):
    """Raised by plugins / registry for client-facing failures. ``status`` maps
    to the HTTP code (404 unknown instrument, 422 bad responses/demographics)."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.message = message
        self.status = status
