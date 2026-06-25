"""Unit tests for the scoring helpers, independent of the equivalence harness."""

from __future__ import annotations

import math

from app.modules.testing.engine.scoring import compute_scores, js_round


def test_js_round_half_toward_positive_infinity():
    # JS Math.round semantics on exact halves
    assert js_round(0.5, 0) == 1
    assert js_round(1.5, 0) == 2
    assert js_round(2.5, 0) == 3
    assert js_round(-0.5, 0) == 0
    assert js_round(-1.5, 0) == -1
    assert js_round(-2.5, 0) == -2


def test_js_round_two_decimals():
    assert js_round(62.005, 2) == 62.01
    assert js_round(64.995, 2) == 65.0
    assert js_round(50.0, 2) == 50.0


def test_js_round_matches_naive_js_formula():
    # mirror Math.round(x*100)/100 across a sweep of values
    for i in range(-100000, 100000, 37):
        x = i / 1000.0
        expected = math.floor(x * 100 + 0.5) / 100
        assert js_round(x, 2) == expected


def test_compute_scores_known_vector(definition):
    # raw == mean on every scale → z == 0 → T == 50 exactly, for both genders.
    # Build responses that yield, per scale, a raw score equal to... we instead
    # verify the formula directly on a deterministic vector: all "no".
    responses = {q["id"]: "no" for q in definition["questions"]}
    result = compute_scores(definition, responses, {"gender": "girl"})

    # all-"no": non-reverse items score 0, reverse items score 1.
    # L has reverse items 40,53,66,79,92 → raw L = 5; others computed below.
    reverse_by_scale: dict[str, int] = {}
    for q in definition["questions"]:
        if q["reverse"]:
            reverse_by_scale[q["scale"]] = reverse_by_scale.get(q["scale"], 0) + 1
    for scale in definition["scale_order"]:
        assert result.raw[scale] == reverse_by_scale.get(scale, 0)

    # spot-check one T value against the hand formula
    norms = definition["norms"]["girl"]["L"]
    z = (result.raw["L"] - norms["mean"]) / norms["sd"]
    assert result.t["L"] == js_round(50 + 10 * z, 2)


def test_compute_scores_accepts_string_keys(definition):
    int_keyed = {q["id"]: "yes" for q in definition["questions"]}
    str_keyed = {str(q["id"]): "yes" for q in definition["questions"]}
    a = compute_scores(definition, int_keyed, {"gender": "boy"})
    b = compute_scores(definition, str_keyed, {"gender": "boy"})
    assert a.raw == b.raw and a.t == b.t
