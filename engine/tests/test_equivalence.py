"""RELEASE BLOCKER — the MMPI plugin must reproduce the original scoring.

Scores thousands of random respondents through both the original logic
(reconstructed from the frozen ``mmpi_original.html``) and the MMPI *plugin*,
asserting identical raw and T-scores. A red test here blocks release.
"""

from __future__ import annotations

import random

N_RANDOM = 5000
QUESTION_IDS = list(range(1, 131))


def test_plugin_matches_original_on_random_respondents(mmpi, reference):
    rng = random.Random(20260625)
    for _ in range(N_RANDOM):
        gender = rng.choice(["girl", "boy"])
        responses = {qid: rng.choice(["yes", "no"]) for qid in QUESTION_IDS}

        ref_raw, ref_t = reference["score"](responses, gender)
        result = mmpi.score(responses, {"gender": gender})

        assert result.raw == ref_raw, f"raw mismatch (gender={gender})"
        assert result.derived["t"] == ref_t, f"T mismatch (gender={gender})"


def test_plugin_matches_original_all_yes_all_no(mmpi, reference):
    for gender in ("girl", "boy"):
        for value in ("yes", "no"):
            responses = {qid: value for qid in QUESTION_IDS}
            ref_raw, ref_t = reference["score"](responses, gender)
            result = mmpi.score(responses, {"gender": gender})
            assert result.raw == ref_raw
            assert result.derived["t"] == ref_t


def test_reference_and_bundled_definition_agree_on_scales(mmpi, reference, definition):
    json_scales = set(definition["scale_order"])
    ref_scales = {scale for scale, _ in reference["questions"].values()}
    assert json_scales == ref_scales
