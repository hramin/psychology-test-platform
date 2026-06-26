"""The one uniform interface every instrument plugin implements.

A plugin is *version-controlled, unit-tested code* with **arbitrarily different
internals** behind this single Protocol — that is what lets MMPI (profile),
MBTI (type, forced-choice), NEO (facets→domains), Gardner (ranked themes) and a
plain Likert index all live as peers. The only thing they share is the contract:
the I/O types in :mod:`engine.contracts`.

Discovery convention (see :mod:`engine.registry`): each plugin **subpackage**
exposes a module-level ``INSTRUMENT`` instance, e.g.::

    # engine/instruments/mmpi/__init__.py
    from engine.instruments.mmpi.plugin import MMPIInstrument
    INSTRUMENT = MMPIInstrument()

Adding a test = drop a new subpackage that exports ``INSTRUMENT``. Nothing in the
engine core or API changes.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.contracts import (
    InstrumentMeta,
    QuestionSchema,
    ResultView,
    ScoreResult,
)


@runtime_checkable
class Instrument(Protocol):
    slug: str
    version: int

    def metadata(self) -> InstrumentMeta:
        """Title, kind, demographics, pagination — everything a client needs
        besides the questions themselves."""
        ...

    def question_schema(self) -> QuestionSchema:
        """The items + answer options to render the test."""
        ...

    def score(self, responses: dict, demographics: dict) -> ScoreResult:
        """Arbitrary per-test computation. Raise ``ValueError`` /
        ``engine.contracts.EngineError`` for bad input."""
        ...

    def build_result(self, score: ScoreResult) -> ResultView:
        """Map this instrument's ``ScoreResult`` into the generic ``ResultView``
        that every renderer understands."""
        ...
