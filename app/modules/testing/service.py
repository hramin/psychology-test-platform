"""Testing service — start / answer / finish an attempt, assemble results.

All business rules and (future) authorization live here, not in templates or
routes. Cross-module reads go through ``catalog.service``; this module never
touches catalog tables directly.
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.modules.catalog import service as catalog
from app.modules.testing.engine.interpret import interpret
from app.modules.testing.engine.scoring import compute_scores
from app.modules.testing.models import Attempt, Interpretation, Score


# ── pagination helpers (data-driven) ────────────────────────────────────────
def page_size(definition: dict) -> int:
    return int(definition.get("pagination", {}).get("page_size", 10))


def total_pages(definition: dict) -> int:
    return max(1, math.ceil(len(definition["questions"]) / page_size(definition)))


def questions_for_page(definition: dict, page: int) -> list[dict]:
    ps = page_size(definition)
    start = page * ps
    return definition["questions"][start : start + ps]


def answer_options(definition: dict) -> list[dict]:
    """Display options (value + label) shown for every question — generalises
    2 / 4 / 6-choice tests. Per-question weights live on each question."""
    return definition["answer_format"]["options"]


def _valid_values(definition: dict) -> dict[int, set[str]]:
    return {q["id"]: {o["value"] for o in q["options"]} for q in definition["questions"]}


# ── demographics (validated generically from the definition) ────────────────
def validate_demographics(definition: dict, raw: dict) -> dict:
    cleaned: dict = {}
    for field in definition["demographics"]:
        key = field["key"]
        label = field.get("label", key)
        value = raw.get(key)
        if isinstance(value, str):
            value = value.strip()

        if value in (None, ""):
            if field.get("required"):
                raise ValidationError(f"فیلد «{label}» الزامی است.")
            cleaned[key] = None
            continue

        ftype = field["type"]
        if ftype == "choice":
            allowed = {o["value"] for o in field["options"]}
            if value not in allowed:
                raise ValidationError(f"مقدار انتخابی برای «{label}» نامعتبر است.")
            cleaned[key] = value
        elif ftype == "int":
            try:
                ivalue = int(value)
            except (TypeError, ValueError):
                raise ValidationError(f"«{label}» باید یک عدد باشد.")
            if "min" in field and ivalue < field["min"]:
                raise ValidationError(
                    f"«{label}» نباید کمتر از {field['min']} باشد."
                )
            if "max" in field and ivalue > field["max"]:
                raise ValidationError(
                    f"«{label}» نباید بیشتر از {field['max']} باشد."
                )
            cleaned[key] = ivalue
        else:  # text
            cleaned[key] = str(value)
    return cleaned


# ── lifecycle ───────────────────────────────────────────────────────────────
async def start_attempt(session: AsyncSession, slug: str, raw_demographics: dict) -> Attempt:
    # ─── TODO(Phase 5): ENTITLEMENT GATE ────────────────────────────────────
    # Replace this stub with the real gate. It must require that the (eventual)
    # user holds an 'available' entitlement for this test_version and flip it
    # available → reserved here, in the same transaction. A crash mid-exam then
    # releases the seat back to 'available' (Beat sweep). For this slice — no
    # auth, no billing — we ALWAYS ALLOW.
    # ────────────────────────────────────────────────────────────────────────
    active = await catalog.get_active(session, slug)
    if active is None:
        raise NotFoundError("آزمون فعالی برای شروع یافت نشد.")
    version_id, definition = active

    demographics = validate_demographics(definition, raw_demographics)
    attempt = Attempt(
        test_version_id=version_id,
        demographics=demographics,
        responses={},
        current_page=0,
        status="in_progress",
    )
    session.add(attempt)
    await session.flush()  # populate attempt.id
    return attempt


async def get_attempt(session: AsyncSession, attempt_id: uuid.UUID) -> Attempt:
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None:
        raise NotFoundError("آزمون موردنظر یافت نشد.")
    return attempt


async def definition_for(session: AsyncSession, attempt: Attempt) -> dict:
    definition = await catalog.get_definition_for_version(
        session, attempt.test_version_id
    )
    if definition is None:
        raise NotFoundError("تعریف آزمون یافت نشد.")
    return definition


async def record_answers(
    attempt: Attempt, definition: dict, page: int, answers: dict[str, str]
) -> list[int]:
    """Merge this page's submitted answers into the attempt (persisted on
    commit → refresh-safe). Rejects tampered/illegal values. Returns the list of
    still-unanswered question ids on this page."""
    questions = questions_for_page(definition, page)
    valid = _valid_values(definition)

    merged = dict(attempt.responses or {})
    for q in questions:
        qid = q["id"]
        value = answers.get(str(qid))
        if value is None:
            continue
        if value not in valid[qid]:
            raise ValidationError("پاسخ ثبت‌شده نامعتبر است.")
        merged[str(qid)] = value

    attempt.responses = merged  # reassignment → SQLAlchemy detects the change
    return [q["id"] for q in questions if str(q["id"]) not in merged]


def clamp_page(definition: dict, page: int) -> int:
    return max(0, min(page, total_pages(definition) - 1))


async def set_answers(
    attempt: Attempt, definition: dict, answers: dict[str, str]
) -> list[int]:
    """Merge an arbitrary set of answers (by question id) into the attempt.

    Unlike ``record_answers`` (page-scoped, used by the HTML flow), this accepts
    answers for any questions — the natural shape for a JSON API. Rejects unknown
    ids and illegal option values. Returns the ids still unanswered overall.
    """
    valid = _valid_values(definition)
    by_id = {q["id"]: q for q in definition["questions"]}

    merged = dict(attempt.responses or {})
    for key, value in answers.items():
        try:
            qid = int(key)
        except (TypeError, ValueError):
            raise ValidationError(f"شناسهٔ سؤال نامعتبر است: {key!r}")
        if qid not in by_id:
            raise ValidationError(f"سؤالی با شناسهٔ {qid} وجود ندارد.")
        if value not in valid[qid]:
            raise ValidationError(f"پاسخ نامعتبر برای سؤال {qid}.")
        merged[str(qid)] = value

    attempt.responses = merged
    return [q["id"] for q in definition["questions"] if str(q["id"]) not in merged]


async def finish_attempt(session: AsyncSession, attempt: Attempt) -> None:
    """Score + interpret + persist. Idempotent (a re-submit is a no-op)."""
    if attempt.status == "completed":
        return

    definition = await definition_for(session, attempt)
    responses = attempt.responses or {}
    missing = [q["id"] for q in definition["questions"] if str(q["id"]) not in responses]
    if missing:
        raise ValidationError("برای پایان آزمون باید به همهٔ سؤالات پاسخ دهید.")

    result = compute_scores(definition, responses, attempt.demographics)
    body = interpret(definition, result.t)

    session.add(Score(attempt_id=attempt.id, raw=result.raw, t=result.t))
    session.add(
        Interpretation(attempt_id=attempt.id, source="rule", body=body)
    )
    attempt.status = "completed"
    attempt.completed_at = datetime.now(timezone.utc)
    # ─── TODO(Phase 5): flip the reserved entitlement → consumed here. ───────


async def get_result(session: AsyncSession, attempt: Attempt) -> dict | None:
    if attempt.status != "completed":
        return None
    definition = await definition_for(session, attempt)
    score = (
        await session.execute(select(Score).where(Score.attempt_id == attempt.id))
    ).scalar_one_or_none()
    interp = (
        await session.execute(
            select(Interpretation).where(Interpretation.attempt_id == attempt.id)
        )
    ).scalar_one_or_none()
    if score is None or interp is None:
        return None

    scale_order = definition["scale_order"]
    validity_keys = [s["key"] for s in definition["scales"] if s["type"] == "validity"]
    norm_field = definition["norm_groups"]["by"]
    norms = definition["norms"][attempt.demographics[norm_field]]

    validity_set = set(validity_keys)
    table = []
    for s in scale_order:
        tv = score.t[s]
        if s in validity_set:
            band = "caution" if tv > 60 else ""
        else:
            band = "severe" if tv >= 70 else ("caution" if tv >= 65 else "")
        table.append(
            {
                "scale": s,
                "raw": score.raw[s],
                "mean": norms[s]["mean"],
                "t": tv,
                "band": band,
            }
        )

    return {
        "attempt": attempt,
        "definition": definition,
        "title": definition["title"],
        "scale_order": scale_order,
        "validity_keys": validity_keys,
        "t": score.t,
        "raw": score.raw,
        "table": table,
        "interpretation": interp.body,
        "demographics": attempt.demographics,
    }


def chart_payload(result: dict) -> dict:
    """Series + guides for the Chart.js profile, faithful to the original."""
    scale_order = result["scale_order"]
    validity = set(result["validity_keys"])
    t = result["t"]
    return {
        "labels": scale_order,
        "validity": [t[s] if s in validity else None for s in scale_order],
        "clinical": [t[s] if s not in validity else None for s in scale_order],
        # divider drawn between the last validity scale (K) and the first
        # clinical scale (Hs)
        "divider_index": scale_order.index("Hs"),
        "y_min": 20,
        "y_max": 90,
        "ref_lines": [
            {"t": 50, "kind": "baseline"},
            {"t": 65, "kind": "caution"},
            {"t": 70, "kind": "severe"},
        ],
    }
