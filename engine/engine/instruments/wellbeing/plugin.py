"""Wellbeing-8 — a deliberately tiny example instrument that exists to **prove the
plugin architecture supports arbitrarily different internals**.

Where MMPI is binary, per-scale, gender-normed, T-scored, and renders a line
*profile*, this one is:

* **4-option Likert** (not binary),
* **no scales, no norm groups, no T-scores** — it sums to a *single index*,
* scored by its **own logic** (reverse items computed inline, not via the shared
  weight-sum helper), and
* rendered as ``ResultView(kind="index")`` with a **gauge** chart.

Same ``Instrument`` interface, completely different calculation and result path —
which is the whole point.
"""

from __future__ import annotations

from datetime import datetime, timezone

from engine.contracts import (
    ChartSpec,
    EngineError,
    InstrumentMeta,
    InterpretationBlock,
    OptionSpec,
    QuestionItem,
    QuestionSchema,
    ReferenceLine,
    ResultItem,
    ResultView,
    ScoreResult,
)

# shared 4-point Likert scale (agreement)
_OPTIONS = [
    {"value": "0", "label": "کاملاً مخالفم"},
    {"value": "1", "label": "مخالفم"},
    {"value": "2", "label": "موافقم"},
    {"value": "3", "label": "کاملاً موافقم"},
]
_MAX_PER_ITEM = 3

# item bank: (id, text, reverse?)
_ITEMS = [
    (1, "از زندگی روزمره‌ام رضایت دارم.", False),
    (2, "نسبت به آینده‌ام امیدوار هستم.", False),
    (3, "اغلب احساس خستگی و بی‌حوصلگی می‌کنم.", True),
    (4, "می‌توانم با مشکلات روزمره به‌خوبی کنار بیایم.", False),
    (5, "اغلب احساس تنهایی می‌کنم.", True),
    (6, "از روابطم با دیگران رضایت دارم.", False),
    (7, "وقتی نگرانم می‌توانم خودم را آرام کنم.", False),
    (8, "احساس می‌کنم کارهایی که انجام می‌دهم ارزشمند است.", False),
]
_MAX_TOTAL = len(_ITEMS) * _MAX_PER_ITEM  # 24

# index → (level, severity, narrative)
_LOW_CUTOFF = 10
_HIGH_CUTOFF = 18


def _level(total: int) -> tuple[str, str, str]:
    if total >= _HIGH_CUTOFF:
        return ("high", "ok", "بهزیستی کلی در سطح مطلوبی گزارش شده است.")
    if total >= _LOW_CUTOFF:
        return ("moderate", "caution",
                "بهزیستی کلی در سطح متوسط است؛ توجه به منابع حمایتی می‌تواند مفید باشد.")
    return ("low", "severe",
            "بهزیستی کلی در سطح پایین گزارش شده است؛ گفت‌وگوی تکمیلی پیشنهاد می‌شود.")


class WellbeingInstrument:
    slug = "wellbeing-8"
    version = 1

    def metadata(self) -> InstrumentMeta:
        return InstrumentMeta(
            slug=self.slug,
            title="شاخص بهزیستی نوجوان (۸ گویه‌ای)",
            version=self.version,
            kind="index",
            demographics=[],  # no norm group → no demographics needed
            page_size=len(_ITEMS),
            answer_format={"type": "likert4", "options": _OPTIONS},
        )

    def question_schema(self) -> QuestionSchema:
        items = [
            QuestionItem(
                id=qid,
                text=text,
                scale=None,  # single-index: items don't belong to scales
                options=[OptionSpec(**o) for o in _OPTIONS],
            )
            for qid, text, _ in _ITEMS
        ]
        return QuestionSchema(
            items=items,
            answer_format={"type": "likert4", "options": _OPTIONS},
            pagination={"page_size": len(_ITEMS)},
        )

    # arbitrary, instrument-specific scoring (NOT the weight-sum helper) ────────
    def score(self, responses: dict, demographics: dict) -> ScoreResult:
        per_item: dict[str, int] = {}
        total = 0
        for qid, _text, reverse in _ITEMS:
            value = responses.get(str(qid), responses.get(qid))
            if value is None:
                raise EngineError(f"missing response for item {qid}", status=422)
            try:
                v = int(value)
            except (TypeError, ValueError):
                raise EngineError(f"non-numeric answer for item {qid}", status=422)
            if not 0 <= v <= _MAX_PER_ITEM:
                raise EngineError(f"answer out of range for item {qid}", status=422)
            contribution = (_MAX_PER_ITEM - v) if reverse else v
            per_item[str(qid)] = contribution
            total += contribution

        level, _severity, _ = _level(total)
        return ScoreResult(
            raw=per_item,
            derived={"index": total, "max": _MAX_TOTAL, "level": level},
        )

    def build_result(self, score: ScoreResult) -> ResultView:
        total = score.derived["index"]
        level, severity, narrative = _level(total)

        item = ResultItem(
            key="wellbeing",
            label="شاخص بهزیستی",
            value=total,
            band=level,
            severity=severity,
            extra={"max": _MAX_TOTAL},
        )

        chart = ChartSpec(
            type="gauge",
            labels=["شاخص بهزیستی"],
            series=[],
            y_min=0,
            y_max=_MAX_TOTAL,
            reference_lines=[
                ReferenceLine(value=_LOW_CUTOFF, kind="threshold", label="مرز پایین"),
                ReferenceLine(value=_HIGH_CUTOFF, kind="threshold", label="مرز بالا"),
            ],
            options={"value": total, "min": 0, "max": _MAX_TOTAL, "level": level},
        )

        return ResultView(
            kind="index",
            summary=f"شاخص بهزیستی: {total} از {_MAX_TOTAL} (سطح: {level})",
            items=[item],
            interpretation=[
                InterpretationBlock(
                    title="تفسیر شاخص بهزیستی",
                    body=narrative,
                    severity=severity,
                )
            ],
            chart=chart,
            meta={
                "slug": self.slug,
                "version": self.version,
                "scored_at": datetime.now(timezone.utc).isoformat(),
            },
        )
