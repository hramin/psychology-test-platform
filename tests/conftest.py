"""Shared test fixtures.

The equivalence test must prove the JSON-driven engine reproduces the *original*
MMPI scoring. To keep the reference genuinely independent of ``mmpi_v1.json``, we
parse the frozen original artifact (``tests/fixtures/mmpi_original.html`` — a
verbatim copy of the verified ``mmpi.html``) and reconstruct its scoring from
that. If the JSON definition ever drifts (a wrong weight, a flipped reverse flag,
a mis-scaled item, a changed norm), the engine output diverges from this
reference and the test turns red — which CLAUDE.md treats as a release blocker.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFINITION_PATH = REPO_ROOT / "mmpi_v1.json"
FIXTURE_HTML = Path(__file__).resolve().parent / "fixtures" / "mmpi_original.html"


def _parse_original_html() -> dict:
    """Reconstruct the original questions + norms from the frozen HTML."""
    html = FIXTURE_HTML.read_text(encoding="utf-8")

    # question bank: id -> (scale, is_reverse)
    q_re = re.compile(
        r'\{\s*id:\s*(\d+),\s*scale:\s*"([^"]+)",\s*isReverse:\s*(true|false)'
    )
    questions = {
        int(qid): (scale, rev == "true") for qid, scale, rev in q_re.findall(html)
    }

    # norm table, split by gender block
    norm_block = re.search(
        r"const normTable\s*=\s*\{(.*?)\n\s*\};", html, re.DOTALL
    ).group(1)
    girl_region, boy_region = re.split(r"\bboy\s*:", norm_block, maxsplit=1)
    entry_re = re.compile(r"(\w+):\s*\{\s*mean:\s*([\d.]+),\s*sd:\s*([\d.]+)\s*\}")

    def parse(region: str) -> dict:
        return {
            scale: {"mean": float(mean), "sd": float(sd)}
            for scale, mean, sd in entry_re.findall(region)
        }

    norms = {"girl": parse(girl_region), "boy": parse(boy_region)}
    return {"questions": questions, "norms": norms}


def _original_score(reference: dict, responses: dict, gender: str):
    """Score exactly as the original ``calculateScores()`` did, including the
    JS ``Math.round((50 + 10*z) * 100) / 100`` rounding — implemented inline so
    this reference does not borrow the engine's ``js_round`` helper."""
    questions = reference["questions"]
    raw: dict[str, int] = {}
    for qid, (scale, is_reverse) in questions.items():
        is_yes = responses[qid] == "yes"
        score = (0 if is_yes else 1) if is_reverse else (1 if is_yes else 0)
        raw[scale] = raw.get(scale, 0) + score

    norms = reference["norms"][gender]
    t: dict[str, float] = {}
    for scale, n in norms.items():
        z = (raw[scale] - n["mean"]) / n["sd"]
        t[scale] = math.floor((50 + 10 * z) * 100 + 0.5) / 100
    return raw, t


@pytest.fixture(scope="session")
def definition() -> dict:
    return json.loads(DEFINITION_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def reference() -> dict:
    parsed = _parse_original_html()

    def score(responses: dict, gender: str):
        return _original_score(parsed, responses, gender)

    return {**parsed, "score": score}
