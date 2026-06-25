"""Rule-based interpretation of a scored profile.

This is the v1 interpretation. It reads the thresholds declared in the test
definition and emits a structured ``body`` that is persisted on an
``interpretations`` row with ``source='rule'``. Later an AI worker can write the
*same row shape* with ``source='ai'`` — adding AI requires zero schema or
web-tier change (the seam already exists here).

Severity encodes the T-score bands and is the single source of truth for the
colours rendered on the result page:
    validity:  elevated when ``T > elevated_if_t_gt``  (MMPI: T > 60)
    clinical:  caution  when ``T >= caution_if_t_gte``  (MMPI: T >= 65)
               severe   when ``T >= severe_if_t_gte``   (MMPI: T >= 70)
"""

from __future__ import annotations


def interpret(definition: dict, t: dict[str, float]) -> dict:
    by_key = {s["key"]: s for s in definition["scales"]}

    validity: list[dict] = []
    clinical: list[dict] = []  # only elevated scales, in scale order
    elevated_count = 0

    for key in definition["scale_order"]:
        scale = by_key[key]
        tv = t[key]
        rule = scale.get("interpretation", {})

        if scale["type"] == "validity":
            flag = tv > rule["elevated_if_t_gt"]
            validity.append(
                {
                    "key": key,
                    "name": scale["name"],
                    "t": tv,
                    "type": "validity",
                    "elevated": flag,
                    "severity": "flag" if flag else "ok",
                    "desc": scale.get("desc"),
                    "high": scale.get("high"),
                }
            )
        else:  # clinical
            if tv >= rule["caution_if_t_gte"]:
                elevated_count += 1
                clinical.append(
                    {
                        "key": key,
                        "name": scale["name"],
                        "t": tv,
                        "type": "clinical",
                        "severity": "severe"
                        if tv >= rule["severe_if_t_gte"]
                        else "caution",
                        "desc": scale.get("desc"),
                        "high": scale.get("high"),
                    }
                )

    return {
        "validity": validity,
        "clinical": clinical,
        "all_normal": elevated_count == 0,
    }
