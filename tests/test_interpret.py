"""Tests for the rule-based interpretation thresholds."""

from __future__ import annotations

from app.modules.testing.engine.interpret import interpret


def _t_all(definition, value: float) -> dict:
    return {s: value for s in definition["scale_order"]}


def test_all_normal_when_everything_at_fifty(definition):
    out = interpret(definition, _t_all(definition, 50.0))
    assert out["all_normal"] is True
    assert out["clinical"] == []
    assert len(out["validity"]) == 3  # L, F, K
    assert all(v["elevated"] is False for v in out["validity"])


def test_validity_flag_is_strict_greater_than_60(definition):
    # exactly 60 is NOT elevated; 60.01 is
    out60 = interpret(definition, _t_all(definition, 60.0))
    assert all(v["elevated"] is False for v in out60["validity"])
    out61 = interpret(definition, _t_all(definition, 60.01))
    assert all(v["elevated"] is True for v in out61["validity"])
    assert all(v["severity"] == "flag" for v in out61["validity"])


def test_clinical_caution_band_at_65(definition):
    out64 = interpret(definition, _t_all(definition, 64.99))
    assert out64["clinical"] == []
    assert out64["all_normal"] is True

    out65 = interpret(definition, _t_all(definition, 65.0))
    assert out65["all_normal"] is False
    # 10 clinical scales elevated (13 total − 3 validity)
    assert len(out65["clinical"]) == 10
    assert all(c["severity"] == "caution" for c in out65["clinical"])


def test_clinical_severe_band_at_70(definition):
    out69 = interpret(definition, _t_all(definition, 69.99))
    assert all(c["severity"] == "caution" for c in out69["clinical"])

    out70 = interpret(definition, _t_all(definition, 70.0))
    assert all(c["severity"] == "severe" for c in out70["clinical"])


def test_clinical_blocks_follow_scale_order(definition):
    out = interpret(definition, _t_all(definition, 80.0))
    clinical_keys = [c["key"] for c in out["clinical"]]
    expected = [
        k
        for k in definition["scale_order"]
        if k not in ("L", "F", "K")
    ]
    assert clinical_keys == expected
