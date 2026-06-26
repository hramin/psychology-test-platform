"""Pure, stdlib-only scoring helpers shared by plugins.

This is the **verbatim migration** of the proven math from the app's
``app/modules/testing/engine/scoring.py`` — re-implemented here so the engine has
zero dependency on the app. The MMPI equivalence test (a release blocker) guards
that this behaviour still matches the original ``mmpi.html``. **Never change the
math.**

Nothing here knows about FastAPI, a database, or any specific test: a plugin
passes in its own data and gets numbers back.
"""

from __future__ import annotations

import math


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


def lookup_response(responses: dict, qid: int):
    """Fetch a question's answer, accepting both int and str keys (JSON / form
    data may key by either). Returns ``None`` if absent."""
    value = responses.get(qid)
    if value is None:
        value = responses.get(str(qid))
    return value


def weighted_raw(
    questions: list[dict],
    responses: dict,
    scales: list[str] | None = None,
) -> dict[str, int]:
    """Sum chosen option weights into per-scale raw scores.

    This single idea generalises 2/4/6-choice single-select tests: every option
    carries a ``weight`` and a scale's raw score is the sum of the chosen
    weights. Reverse scoring is encoded in the weights themselves (e.g. MMPI
    reverse items flip yes/no between 1 and 0), so there is no special-casing
    here. Every declared question must have a response.
    """
    weight = {
        q["id"]: {o["value"]: o["weight"] for o in q["options"]} for q in questions
    }
    raw: dict[str, int] = {s: 0 for s in (scales or [])}
    for q in questions:
        qid = q["id"]
        value = lookup_response(responses, qid)
        if value is None:
            raise ValueError(f"missing response for question {qid}")
        try:
            w = weight[qid][value]
        except KeyError as exc:
            raise ValueError(f"invalid option '{value}' for question {qid}") from exc
        raw[q["scale"]] = raw.get(q["scale"], 0) + w
    return raw


def norm_t(
    raw: dict[str, int],
    norms: dict[str, dict],
    scale_order: list[str],
    round_decimals: int = 2,
) -> dict[str, float]:
    """Standardise raw scores to T-scores: ``T = js_round(50 + 10*z, n)`` where
    ``z = (raw - mean) / sd``, using a chosen norm group's mean/sd per scale."""
    t: dict[str, float] = {}
    for s in scale_order:
        mean = norms[s]["mean"]
        sd = norms[s]["sd"]
        z = (raw[s] - mean) / sd
        t[s] = js_round(50 + 10 * z, round_decimals)
    return t
