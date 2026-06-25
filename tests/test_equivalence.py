"""RELEASE BLOCKER — the engine must reproduce the original MMPI scoring.

Scores thousands of random respondents through both the original logic
(reconstructed from the frozen ``mmpi_original.html``) and the JSON-driven
engine, asserting identical raw and T-scores. A red test here blocks release.
"""

from __future__ import annotations

import random

from app.modules.testing.engine.scoring import compute_scores

N_RANDOM = 5000
QUESTION_IDS = list(range(1, 131))


def test_engine_matches_original_on_random_respondents(definition, reference):
    rng = random.Random(20260624)
    for _ in range(N_RANDOM):
        gender = rng.choice(["girl", "boy"])
        responses = {qid: rng.choice(["yes", "no"]) for qid in QUESTION_IDS}

        ref_raw, ref_t = reference["score"](responses, gender)
        result = compute_scores(definition, responses, {"gender": gender})

        assert result.raw == ref_raw, f"raw mismatch (gender={gender})"
        assert result.t == ref_t, f"T mismatch (gender={gender})"


def test_engine_matches_original_all_yes_all_no(definition, reference):
    for gender in ("girl", "boy"):
        for value in ("yes", "no"):
            responses = {qid: value for qid in QUESTION_IDS}
            ref_raw, ref_t = reference["score"](responses, gender)
            result = compute_scores(definition, responses, {"gender": gender})
            assert result.raw == ref_raw
            assert result.t == ref_t


def test_reference_and_engine_agree_on_scale_membership(definition, reference):
    # every scale defined in the JSON is exercised by the reference too
    json_scales = set(definition["scale_order"])
    ref_scales = {scale for scale, _ in reference["questions"].values()}
    assert json_scales == ref_scales
