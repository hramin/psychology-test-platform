"""Generic, data-driven scoring engine.

This module is intentionally pure (stdlib only) and knows nothing about the web
tier, the database, or any specific test. It reads a test *definition* dict (the
``mmpi_v1.json`` shape) and scores a set of responses. The same code path serves
any single-choice test with 2 / 4 / 6 options because every option carries a
``weight`` and a scale's raw score is just the sum of the chosen weights.

The MMPI scoring reproduced here is verified bit-for-bit against the original
``mmpi.html`` (see ``tests/test_equivalence.py`` — a release blocker). Never
change the scoring math.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


def js_round(value: float, ndigits: int) -> float:
    """Round like JavaScript ``Math.round`` (half toward +infinity).

    The original instrument computed ``Math.round((50 + 10*z) * 100) / 100`` in
    the browser. Python's built-in ``round`` uses banker's rounding (half to
    even), which diverges on exact halves, so we reproduce the JS behaviour
    explicitly: ``floor(value * 10**n + 0.5) / 10**n``.

    Examples (ndigits=0): 0.5 -> 1, 2.5 -> 3, -0.5 -> 0, -1.5 -> -1.
    """
    factor = 10 ** ndigits
    return math.floor(value * factor + 0.5) / factor


@dataclass
class ScoreResult:
    raw: dict[str, int]
    t: dict[str, float]


def _norm_group(definition: dict, demographics: dict) -> str:
    """Resolve the norm-group key from demographics (e.g. gender -> 'girl')."""
    by = definition["norm_groups"]["by"]
    try:
        return demographics[by]
    except KeyError as exc:  # pragma: no cover - guarded by the service layer
        raise ValueError(f"demographics missing norm field '{by}'") from exc


def compute_scores(
    definition: dict,
    responses: dict,
    demographics: dict,
) -> ScoreResult:
    """Compute raw scale sums and standardized T-scores.

    ``responses`` maps question id -> chosen option value (e.g. {1: "yes"}).
    Keys may be ints or strings; both are accepted. Every question declared in
    the definition must have a response.
    """
    scale_order = definition["scale_order"]
    questions = definition["questions"]

    # option weights → raw scale sums (this line generalises 2/4/6 choices)
    weight = {
        q["id"]: {o["value"]: o["weight"] for o in q["options"]} for q in questions
    }

    raw: dict[str, int] = {s: 0 for s in scale_order}
    for q in questions:
        qid = q["id"]
        # accept both int and str keys coming from JSON / form data
        value = responses.get(qid)
        if value is None:
            value = responses.get(str(qid))
        if value is None:
            raise ValueError(f"missing response for question {qid}")
        try:
            raw[q["scale"]] += weight[qid][value]
        except KeyError as exc:
            raise ValueError(
                f"invalid option '{value}' for question {qid}"
            ) from exc

    group = _norm_group(definition, demographics)
    norms = definition["norms"][group]
    nd = definition["tscore"]["round_decimals"]

    t: dict[str, float] = {}
    for s in scale_order:
        mean = norms[s]["mean"]
        sd = norms[s]["sd"]
        z = (raw[s] - mean) / sd
        t[s] = js_round(50 + 10 * z, nd)

    return ScoreResult(raw=raw, t=t)
