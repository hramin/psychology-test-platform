"""Plugin discovery + lookup.

At import time we walk the :mod:`engine.instruments` package, import every direct
subpackage, and collect its exported ``INSTRUMENT`` instance into an in-memory
registry. The engine is stateless: this map is the whole "state", rebuilt fresh
on every process start.

Indexed two ways:
  * ``_by_key[(slug, version)]`` — exact version lookup.
  * ``_latest[slug]`` — highest registered version, used when a caller omits the
    version (so ``/score`` and ``/schema`` default to the newest plugin).
"""

from __future__ import annotations

import importlib
import pkgutil

from engine import instruments as _instruments_pkg
from engine.contracts import EngineError, InstrumentSummary
from engine.instruments.base import Instrument

_by_key: dict[tuple[str, int], Instrument] = {}
_latest: dict[str, int] = {}


def _register(inst: Instrument) -> None:
    key = (inst.slug, inst.version)
    if key in _by_key:
        raise RuntimeError(f"duplicate instrument registered: {key}")
    _by_key[key] = inst
    if inst.slug not in _latest or inst.version > _latest[inst.slug]:
        _latest[inst.slug] = inst.version


def discover() -> None:
    """Import every instrument subpackage and register its ``INSTRUMENT``.

    Idempotent: safe to call more than once (clears and rebuilds)."""
    _by_key.clear()
    _latest.clear()
    for mod in pkgutil.iter_modules(_instruments_pkg.__path__):
        if not mod.ispkg:
            continue  # only subpackages are instruments; base.py etc. are skipped
        module = importlib.import_module(
            f"{_instruments_pkg.__name__}.{mod.name}"
        )
        inst = getattr(module, "INSTRUMENT", None)
        if inst is None:
            continue
        if not isinstance(inst, Instrument):
            raise RuntimeError(
                f"{module.__name__}.INSTRUMENT does not satisfy the Instrument "
                f"interface"
            )
        _register(inst)


def get(slug: str, version: int | None = None) -> Instrument:
    """Resolve an instrument by slug (+ optional version). Raises ``EngineError``
    (404) if unknown."""
    if version is None:
        version = _latest.get(slug)
        if version is None:
            raise EngineError(f"unknown instrument '{slug}'", status=404)
    inst = _by_key.get((slug, version))
    if inst is None:
        raise EngineError(
            f"unknown instrument '{slug}' version {version}", status=404
        )
    return inst


def all_instruments() -> list[Instrument]:
    return list(_by_key.values())


def list_summaries() -> list[InstrumentSummary]:
    """One row per slug at its latest version (what ``GET /instruments`` returns)."""
    out: list[InstrumentSummary] = []
    for slug, version in sorted(_latest.items()):
        inst = _by_key[(slug, version)]
        meta = inst.metadata()
        out.append(
            InstrumentSummary(
                slug=slug, version=version, title=meta.title, kind=meta.kind
            )
        )
    return out


# discover plugins on import so the API has a populated registry immediately
discover()
