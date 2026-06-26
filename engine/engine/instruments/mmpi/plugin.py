"""MMPI-Teen-13 — the first instrument plugin.

Wraps the verified definition (`mmpi_v1.json`, bundled alongside this module) and
reproduces the original `mmpi.html` **exactly**, both the calculation and the
result/chart:

* **score()** — option-weight raw sums (yes=1/no=0, already flipped in the JSON
  for the 11 reverse items) → gender-norm T-scores ``round(50 + 10*z, 2)`` with
  JS half-up rounding (``engine.scoring.js_round``).
* **build_result()** — ``ResultView(kind="profile")``: a line chart (y 20–90,
  reference lines 50/65/70, vertical divider between K and Hs, validity vs
  clinical as two series), per-scale items with the original's bands (validity
  flag ``T>60``; clinical caution ``T≥65`` / severe ``T≥70``), and the same
  validity/clinical interpretation blocks and Persian wording.

The MMPI equivalence test is a release blocker: it scores thousands of random
respondents through this plugin and through a reconstruction of the frozen
original and asserts zero divergence. **Never change the scoring math.**
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from engine.contracts import (
    ChartDivider,
    ChartSeries,
    ChartSpec,
    DemographicField,
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
from engine.contracts import EngineError
from engine.scoring import norm_t, weighted_raw

_DATA_PATH = Path(__file__).resolve().parent / "mmpi_v1.json"

# original generateInterpretation() status sentences (preserved verbatim)
_VALIDITY_NORMAL = "🟢 شاخص در محدوده نرمال گروه هنجار نوجوانان قرار دارد."
_ALL_NORMAL = (
    "شواهد بالینی حاکی از نرمال بودن کل پروفایل مراجع است. نمرات مراجع در تمامی "
    "مقیاس‌های بالینی و تکمیلی پایین‌تر از خط برش T=65 ثبت گردیده است."
)


class MMPIInstrument:
    def __init__(self) -> None:
        self._def: dict = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
        self.slug: str = self._def["slug"]
        self.version: int = int(self._def["version"])
        self._by_key = {s["key"]: s for s in self._def["scales"]}

    # ── advertise ────────────────────────────────────────────────────────────
    def metadata(self) -> InstrumentMeta:
        d = self._def
        return InstrumentMeta(
            slug=d["slug"],
            title=d["title"],
            version=self.version,
            kind="profile",
            demographics=[
                DemographicField(
                    key=f["key"],
                    label=f["label"],
                    type=f["type"],
                    required=f.get("required", False),
                    options=[OptionSpec(**o) for o in f["options"]]
                    if f.get("options")
                    else None,
                    min=f.get("min"),
                    max=f.get("max"),
                    drives_norm_group=f.get("drives_norm_group", False),
                )
                for f in d["demographics"]
            ],
            page_size=d.get("pagination", {}).get("page_size"),
            answer_format=d.get("answer_format"),
        )

    def question_schema(self) -> QuestionSchema:
        d = self._def
        # per-item options carry display labels from the shared answer format;
        # scoring weights stay internal to score() and are not leaked here.
        label_by_value = {o["value"]: o["label"] for o in d["answer_format"]["options"]}
        items = [
            QuestionItem(
                id=q["id"],
                text=q["text"],
                scale=q["scale"],
                options=[
                    OptionSpec(value=o["value"], label=label_by_value.get(o["value"], ""))
                    for o in q["options"]
                ],
            )
            for q in d["questions"]
        ]
        return QuestionSchema(
            items=items,
            answer_format=d.get("answer_format"),
            pagination=d.get("pagination"),
        )

    # ── compute (verbatim math) ──────────────────────────────────────────────
    def score(self, responses: dict, demographics: dict) -> ScoreResult:
        d = self._def
        scale_order = d["scale_order"]

        by = d["norm_groups"]["by"]
        group = demographics.get(by)
        if group is None:
            raise EngineError(f"demographics missing norm field '{by}'", status=422)
        norms = d["norms"].get(group)
        if norms is None:
            raise EngineError(f"unknown norm group '{group}' for '{by}'", status=422)

        try:
            raw = weighted_raw(d["questions"], responses, scale_order)
        except ValueError as exc:
            raise EngineError(str(exc), status=422) from exc

        nd = d["tscore"]["round_decimals"]
        t = norm_t(raw, norms, scale_order, nd)

        return ScoreResult(
            raw=raw,
            derived={
                "t": t,
                "norm_group": group,
                "means": {s: norms[s]["mean"] for s in scale_order},
            },
        )

    # ── render → generic ResultView ──────────────────────────────────────────
    def _band(self, key: str, tv: float) -> str:
        """Numeric-table cell band, faithful to the original table colouring."""
        if self._by_key[key]["type"] == "validity":
            return "caution" if tv > 60 else ""
        return "severe" if tv >= 70 else ("caution" if tv >= 65 else "")

    def build_result(self, score: ScoreResult) -> ResultView:
        d = self._def
        scale_order = d["scale_order"]
        t: dict[str, float] = score.derived["t"]
        means: dict[str, float] = score.derived["means"]
        raw: dict[str, int] = score.raw
        validity_keys = [s["key"] for s in d["scales"] if s["type"] == "validity"]
        validity_set = set(validity_keys)

        items: list[ResultItem] = []
        interpretation: list[InterpretationBlock] = []
        elevated_clinical = 0
        flagged_validity = 0

        for key in scale_order:
            scale = self._by_key[key]
            tv = t[key]
            is_validity = scale["type"] == "validity"

            if is_validity:
                flag = tv > scale["interpretation"]["elevated_if_t_gt"]  # T > 60
                severity = "flag" if flag else "ok"
                flagged_validity += int(flag)
                status = (
                    f"🔴 شاخص پدیدار بالا: {scale['high']}"
                    if flag
                    else _VALIDITY_NORMAL
                )
                interpretation.append(
                    InterpretationBlock(
                        title=f"{scale['name']} — (نمره T: {tv})",
                        body=f"{scale['desc']} {status}",
                        severity=severity,
                        section="validity",
                    )
                )
            else:
                rule = scale["interpretation"]
                if tv >= rule["caution_if_t_gte"]:  # T >= 65
                    severity = "severe" if tv >= rule["severe_if_t_gte"] else "caution"
                    elevated_clinical += 1
                    badge = (
                        "⚠️ وضعیت اختلال شدید"
                        if severity == "severe"
                        else "🔍 وضعیت بالینی محتاط"
                    )
                    interpretation.append(
                        InterpretationBlock(
                            title=f"{scale['name']} — (نمره T: {tv}) {badge}",
                            body=(
                                f"تعریف مقیاس: {scale['desc']} "
                                f"تفسیر بالینی: {scale['high']}"
                            ),
                            severity=severity,
                            section="clinical",
                        )
                    )
                else:
                    severity = "ok"

            items.append(
                ResultItem(
                    key=key,
                    label=scale["name"],
                    value=tv,
                    band=self._band(key, tv),
                    severity=severity,
                    extra={"raw": raw[key], "mean": means[key], "type": scale["type"]},
                )
            )

        if elevated_clinical == 0:
            interpretation.append(
                InterpretationBlock(
                    title="جمع‌بندی مقیاس‌های بالینی",
                    body=_ALL_NORMAL,
                    severity="ok",
                    section="clinical",
                )
            )

        chart = ChartSpec(
            type="line",
            labels=scale_order,
            series=[
                ChartSeries(
                    name="مقیاس‌های روایی",
                    data=[t[s] if s in validity_set else None for s in scale_order],
                ),
                ChartSeries(
                    name="مقیاس‌های بالینی",
                    data=[t[s] if s not in validity_set else None for s in scale_order],
                ),
            ],
            y_min=20,
            y_max=90,
            reference_lines=[
                ReferenceLine(value=50, kind="baseline",
                              label="خط پایه / میانگین جامعه (T=50)"),
                ReferenceLine(value=65, kind="caution", label="خط مرز بالینی (T=65)"),
                ReferenceLine(value=70, kind="severe", label="مرز اختلال شدید (T=70)"),
            ],
            # divider drawn between the last validity scale (K) and first clinical (Hs)
            dividers=[
                ChartDivider(
                    index=scale_order.index("Hs"),
                    left_label="مقیاس‌های روایی",
                    right_label="مقیاس‌های بالینی و تکمیلی",
                )
            ],
        )

        summary = (
            f"تعداد مقیاس‌های بالینی برجسته (T≥۶۵): {elevated_clinical} — "
            f"تعداد شاخص‌های روایی پدیدار (T>۶۰): {flagged_validity}"
        )

        return ResultView(
            kind="profile",
            summary=summary,
            items=items,
            interpretation=interpretation,
            chart=chart,
            meta={
                "slug": self.slug,
                "version": self.version,
                "norm_group": score.derived["norm_group"],
                "scored_at": datetime.now(timezone.utc).isoformat(),
            },
        )
