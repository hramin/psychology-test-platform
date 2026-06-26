"""The registry discovers plugins and resolves them by slug/version."""

from __future__ import annotations

import pytest

from engine import registry
from engine.contracts import EngineError
from engine.instruments.base import Instrument


def test_discovery_finds_both_plugins():
    slugs = {inst.slug for inst in registry.all_instruments()}
    assert {"mmpi-teen-13", "wellbeing-8"} <= slugs


def test_all_instruments_satisfy_the_interface():
    for inst in registry.all_instruments():
        assert isinstance(inst, Instrument)


def test_get_by_slug_defaults_to_latest_version():
    inst = registry.get("mmpi-teen-13")
    assert inst.slug == "mmpi-teen-13"
    assert inst.version == 1


def test_get_exact_version():
    inst = registry.get("wellbeing-8", 1)
    assert inst.slug == "wellbeing-8" and inst.version == 1


def test_unknown_slug_raises_404():
    with pytest.raises(EngineError) as exc:
        registry.get("does-not-exist")
    assert exc.value.status == 404


def test_unknown_version_raises_404():
    with pytest.raises(EngineError) as exc:
        registry.get("mmpi-teen-13", 99)
    assert exc.value.status == 404


def test_list_summaries_one_row_per_slug():
    summaries = registry.list_summaries()
    by_slug = {s.slug: s for s in summaries}
    assert by_slug["mmpi-teen-13"].kind == "profile"
    assert by_slug["wellbeing-8"].kind == "index"
