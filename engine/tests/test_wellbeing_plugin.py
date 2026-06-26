"""The wellbeing example plugin — a totally different calculation + result path,
proving the architecture is not MMPI-shaped."""

from __future__ import annotations

import pytest

from engine.contracts import EngineError

ITEM_IDS = list(range(1, 9))


def _answers(value: str) -> dict:
    return {str(i): value for i in ITEM_IDS}


# ── schema ───────────────────────────────────────────────────────────────────
def test_metadata_is_an_index_with_four_options(wellbeing):
    meta = wellbeing.metadata()
    assert meta.kind == "index"
    assert meta.demographics == []  # no norm group
    assert len(meta.answer_format["options"]) == 4


def test_schema_items_have_no_scale(wellbeing):
    schema = wellbeing.question_schema()
    assert len(schema.items) == 8
    assert all(item.scale is None for item in schema.items)
    assert all(len(item.options) == 4 for item in schema.items)


# ── scoring (single index, reverse handling) ─────────────────────────────────
def test_all_min_answers(wellbeing):
    # positive items (6) → 0; reverse items (2) → 3 each → total 6
    result = wellbeing.score(_answers("0"), {})
    assert result.derived["index"] == 6
    assert result.derived["max"] == 24
    assert result.derived["level"] == "low"


def test_all_max_answers(wellbeing):
    # positive items (6) → 3 each = 18; reverse items (2) → 0 → total 18
    result = wellbeing.score(_answers("3"), {})
    assert result.derived["index"] == 18
    assert result.derived["level"] == "high"


def test_true_maximum_index(wellbeing):
    # positive "3", reverse ("3" and "5") answered "0" → 18 + 3 + 3 = 24
    responses = {str(i): "3" for i in ITEM_IDS}
    responses["3"] = "0"
    responses["5"] = "0"
    result = wellbeing.score(responses, {})
    assert result.derived["index"] == 24


def test_reverse_item_is_flipped(wellbeing):
    result = wellbeing.score(_answers("0"), {})
    assert result.raw["3"] == 3  # item 3 is reverse: answer 0 → contributes 3
    assert result.raw["1"] == 0  # item 1 is positive: answer 0 → contributes 0


def test_moderate_band(wellbeing):
    # craft a total of ~12: answer "1" everywhere → positive 6*1=6, reverse 2*2=4 → 10
    result = wellbeing.score(_answers("1"), {})
    assert result.derived["index"] == 10
    assert result.derived["level"] == "moderate"


@pytest.mark.parametrize("bad", [{}, {"1": "x"}, {"1": "9"}])
def test_bad_input_is_422(wellbeing, bad):
    responses = _answers("1")
    responses.update(bad)
    if bad == {}:
        responses.pop("1")  # missing item
    with pytest.raises(EngineError) as exc:
        wellbeing.score(responses, {})
    assert exc.value.status == 422


# ── build_result (index → gauge) ─────────────────────────────────────────────
def test_result_view_is_index_with_gauge(wellbeing):
    view = wellbeing.build_result(wellbeing.score(_answers("0"), {}))
    assert view.kind == "index"
    assert len(view.items) == 1
    assert view.chart.type == "gauge"
    assert view.chart.options["max"] == 24
    assert view.chart.options["value"] == 6
    assert view.items[0].severity == "severe"  # low wellbeing → concern colour
