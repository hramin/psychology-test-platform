"""Structural lock on mmpi_v1.json — guards the definition against drift."""

from __future__ import annotations

REVERSE_IDS = {40, 53, 66, 79, 92, 124, 125, 126, 127, 128, 130}
VALIDITY = {"L", "F", "K"}
CLINICAL = {"Hs", "D", "Hy", "Pa", "Sc", "Ma", "Pd", "Si", "PK", "A"}


def test_has_130_questions(definition):
    assert len(definition["questions"]) == 130
    ids = sorted(q["id"] for q in definition["questions"])
    assert ids == list(range(1, 131))


def test_13_scales_each_with_10_items(definition):
    assert len(definition["scale_order"]) == 13
    assert set(definition["scale_order"]) == VALIDITY | CLINICAL
    counts: dict[str, int] = {}
    for q in definition["questions"]:
        counts[q["scale"]] = counts.get(q["scale"], 0) + 1
    assert all(counts[s] == 10 for s in definition["scale_order"])


def test_reverse_items_exact(definition):
    reverse = {q["id"] for q in definition["questions"] if q["reverse"]}
    assert reverse == REVERSE_IDS


def test_option_weights_are_consistent(definition):
    for q in definition["questions"]:
        weights = {o["value"]: o["weight"] for o in q["options"]}
        assert set(weights) == {"yes", "no"}
        assert all(isinstance(w, int) for w in weights.values())
        if q["reverse"]:
            assert weights == {"yes": 0, "no": 1}
        else:
            assert weights == {"yes": 1, "no": 0}


def test_norms_present_for_both_genders(definition):
    for gender in ("girl", "boy"):
        norms = definition["norms"][gender]
        assert set(norms) == VALIDITY | CLINICAL
        for s in definition["scale_order"]:
            assert norms[s]["sd"] > 0


def test_scale_types_and_thresholds(definition):
    by_key = {s["key"]: s for s in definition["scales"]}
    for k in VALIDITY:
        assert by_key[k]["type"] == "validity"
        assert by_key[k]["interpretation"]["elevated_if_t_gt"] == 60
    for k in CLINICAL:
        assert by_key[k]["type"] == "clinical"
        assert by_key[k]["interpretation"]["caution_if_t_gte"] == 65
        assert by_key[k]["interpretation"]["severe_if_t_gte"] == 70


def test_demographics_drive_norm_group(definition):
    driver = [d for d in definition["demographics"] if d.get("drives_norm_group")]
    assert len(driver) == 1
    assert driver[0]["key"] == definition["norm_groups"]["by"] == "gender"
