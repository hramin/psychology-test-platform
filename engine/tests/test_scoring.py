"""Unit tests for the pure scoring helpers (migrated verbatim from the app)."""

from __future__ import annotations

import math

import pytest

from engine.scoring import js_round, norm_t, weighted_raw


def test_js_round_half_toward_positive_infinity():
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
    for i in range(-100000, 100000, 37):
        x = i / 1000.0
        expected = math.floor(x * 100 + 0.5) / 100
        assert js_round(x, 2) == expected


def test_weighted_raw_sums_option_weights():
    questions = [
        {"id": 1, "scale": "A", "options": [{"value": "yes", "weight": 1},
                                            {"value": "no", "weight": 0}]},
        {"id": 2, "scale": "A", "options": [{"value": "yes", "weight": 0},
                                            {"value": "no", "weight": 1}]},  # reverse
        {"id": 3, "scale": "B", "options": [{"value": "yes", "weight": 1},
                                            {"value": "no", "weight": 0}]},
    ]
    raw = weighted_raw(questions, {"1": "yes", "2": "yes", "3": "no"}, ["A", "B"])
    assert raw == {"A": 1, "B": 0}  # q1 yes→1, q2 (reverse) yes→0, q3 no→0


def test_weighted_raw_rejects_missing_and_bad_values():
    questions = [
        {"id": 1, "scale": "A", "options": [{"value": "yes", "weight": 1},
                                            {"value": "no", "weight": 0}]},
    ]
    with pytest.raises(ValueError):
        weighted_raw(questions, {}, ["A"])
    with pytest.raises(ValueError):
        weighted_raw(questions, {"1": "maybe"}, ["A"])


def test_norm_t_zero_z_is_fifty():
    raw = {"A": 5}
    norms = {"A": {"mean": 5.0, "sd": 2.0}}
    assert norm_t(raw, norms, ["A"], 2) == {"A": 50.0}
