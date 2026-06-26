"""The MMPI plugin: schema, scoring, and a faithful ResultView/chart."""

from __future__ import annotations

import pytest

from engine.contracts import EngineError, ScoreResult
from engine.scoring import js_round

QUESTION_IDS = list(range(1, 131))
SCALE_ORDER = ["L", "F", "K", "Hs", "D", "Hy", "Pa", "Sc", "Ma", "Pd", "Si", "PK", "A"]


# ── metadata / schema ────────────────────────────────────────────────────────
def test_metadata_is_a_profile_with_gender_norm(mmpi):
    meta = mmpi.metadata()
    assert meta.slug == "mmpi-teen-13"
    assert meta.kind == "profile"
    assert meta.page_size == 10
    norm_fields = [d for d in meta.demographics if d.drives_norm_group]
    assert [d.key for d in norm_fields] == ["gender"]


def test_schema_has_130_binary_items(mmpi):
    schema = mmpi.question_schema()
    assert len(schema.items) == 130
    for item in schema.items:
        assert {o.value for o in item.options} == {"yes", "no"}


# ── scoring (known vectors) ──────────────────────────────────────────────────
def test_all_no_raw_equals_reverse_counts(mmpi, definition):
    responses = {str(q["id"]): "no" for q in definition["questions"]}
    result = mmpi.score(responses, {"gender": "girl"})

    reverse_by_scale: dict[str, int] = {}
    for q in definition["questions"]:
        if q["reverse"]:
            reverse_by_scale[q["scale"]] = reverse_by_scale.get(q["scale"], 0) + 1
    for scale in SCALE_ORDER:
        assert result.raw[scale] == reverse_by_scale.get(scale, 0)

    # spot-check one T value against the hand formula
    norms = definition["norms"]["girl"]["L"]
    z = (result.raw["L"] - norms["mean"]) / norms["sd"]
    assert result.derived["t"]["L"] == js_round(50 + 10 * z, 2)


def test_score_records_norm_group_and_means(mmpi, definition):
    responses = {str(q["id"]): "yes" for q in definition["questions"]}
    result = mmpi.score(responses, {"gender": "boy"})
    assert result.derived["norm_group"] == "boy"
    assert result.derived["means"]["A"] == definition["norms"]["boy"]["A"]["mean"]


def test_missing_gender_is_a_422(mmpi):
    with pytest.raises(EngineError) as exc:
        mmpi.score({str(i): "yes" for i in QUESTION_IDS}, {})
    assert exc.value.status == 422


def test_missing_response_is_a_422(mmpi):
    with pytest.raises(EngineError) as exc:
        mmpi.score({"1": "yes"}, {"gender": "girl"})  # 129 missing
    assert exc.value.status == 422


# ── build_result (faithful to the original) ──────────────────────────────────
def _scored(t_value: float) -> ScoreResult:
    """A ScoreResult with every scale at one T value (for band assertions)."""
    return ScoreResult(
        raw={s: 0 for s in SCALE_ORDER},
        derived={
            "t": {s: t_value for s in SCALE_ORDER},
            "norm_group": "girl",
            "means": {s: 0.0 for s in SCALE_ORDER},
        },
    )


def test_result_view_is_profile_with_line_chart(mmpi):
    view = mmpi.build_result(_scored(50.0))
    assert view.kind == "profile"
    assert len(view.items) == 13
    chart = view.chart
    assert chart.type == "line"
    assert chart.y_min == 20 and chart.y_max == 90
    assert {rl.value for rl in chart.reference_lines} == {50, 65, 70}
    assert len(chart.series) == 2  # validity vs clinical
    # vertical divider sits between K and Hs
    assert chart.dividers[0].index == SCALE_ORDER.index("Hs")


def test_chart_series_split_validity_and_clinical(mmpi):
    view = mmpi.build_result(_scored(55.0))
    validity, clinical = view.chart.series
    # validity series carries L/F/K (first 3) and None elsewhere
    assert validity.data[:3] == [55.0, 55.0, 55.0]
    assert all(v is None for v in validity.data[3:])
    assert all(c is None for c in clinical.data[:3])


def test_all_normal_at_fifty(mmpi):
    view = mmpi.build_result(_scored(50.0))
    # no clinical block elevated → the "all normal" summary block is present
    clinical_blocks = [b for b in view.interpretation if b.section == "clinical"]
    assert len(clinical_blocks) == 1
    assert clinical_blocks[0].severity == "ok"
    assert all(i.severity in ("ok",) for i in view.items)


def test_validity_flag_strictly_above_60(mmpi):
    assert all(i.severity == "ok" for i in mmpi.build_result(_scored(60.0)).items
               if i.extra["type"] == "validity")
    flagged = [i for i in mmpi.build_result(_scored(60.01)).items
               if i.extra["type"] == "validity"]
    assert flagged and all(i.severity == "flag" for i in flagged)


def test_clinical_caution_and_severe_bands(mmpi):
    caution = [i for i in mmpi.build_result(_scored(65.0)).items
               if i.extra["type"] == "clinical"]
    assert caution and all(i.severity == "caution" and i.band == "caution"
                           for i in caution)
    severe = [i for i in mmpi.build_result(_scored(70.0)).items
              if i.extra["type"] == "clinical"]
    assert severe and all(i.severity == "severe" and i.band == "severe"
                          for i in severe)


def test_summary_counts_elevations(mmpi):
    view = mmpi.build_result(_scored(72.0))
    # 10 clinical scales severe, 3 validity flagged
    clinical_blocks = [b for b in view.interpretation if b.section == "clinical"]
    validity_blocks = [b for b in view.interpretation if b.section == "validity"]
    assert len(clinical_blocks) == 10
    assert len(validity_blocks) == 3
