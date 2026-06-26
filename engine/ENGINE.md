# Scoring Engine

A **standalone, stateless** FastAPI service that loads **instrument plugins** and exposes a scoring API.
It has **no database and no dependency on the app**. The app (and, later, the B2B gateway and a React UI)
calls it over HTTP; the engine itself stores nothing.

> Why a separate service? It's pure compute, shares no DB transaction, and has multiple consumers. It is the
> *only* thing extracted from the modular monolith (see `ARCHITECTURE_AND_PLAN.md` §0). Everything
> transactional — identity, billing/wallet, attempts/results — stays in the app.

---

## Layout

```
engine/
  pyproject.toml                  # deps + packaging + pytest config
  Dockerfile                      # own image; build context is engine/
  ENGINE.md                       # this file
  engine/
    app.py                        # FastAPI app + endpoints
    registry.py                   # plugin discovery + lookup
    contracts.py                  # the shared types (Instrument I/O + ResultView)
    scoring.py                    # pure scoring helpers (js_round, weighted_raw, norm_t)
    instruments/
      base.py                     # the Instrument Protocol
      mmpi/                       # plugin: MMPI-Teen-13 (bundles mmpi_v1.json)
      wellbeing/                  # plugin: a tiny 4-option Likert single-index example
  tests/                          # unit + API + equivalence (release blocker) tests
```

---

## The contract

Every instrument is a plugin implementing **one** interface (`engine/instruments/base.py`):

```python
class Instrument(Protocol):
    slug: str
    version: int
    def metadata(self) -> InstrumentMeta: ...        # title, kind, demographics, page_size
    def question_schema(self) -> QuestionSchema: ...  # items + options (2/4/6 or forced-choice)
    def score(self, responses, demographics) -> ScoreResult: ...   # ← arbitrary per-test logic
    def build_result(self, score) -> ResultView: ...  # map to the one generic render model
```

- `ScoreResult = {raw: dict, derived: dict}` — instrument-specific numbers (MMPI: per-scale raw + T-scores;
  Wellbeing: per-item points + a single index). The shape *inside* is the plugin's business.
- Plugins are **version-controlled, unit-tested code** with arbitrarily different internals. The only thing
  they share is the **output**.

### The unifying output: `ResultView`

```python
ResultView:
    kind: str                 # "profile" | "type" | "index" | "themes"
    summary: str
    items: list[ResultItem]   # {key, label, value, band?, severity?, extra}
    interpretation: list[InterpretationBlock]   # {title, body, severity?, section?}
    chart: ChartSpec | None   # {type: line|bar|radar|gauge, labels, series, y_min/max,
                              #  reference_lines[], dividers[], options}
    meta: dict                # slug, version, scored_at, …
```

One `ResultView` → one generic renderer for HTMX, React, and the PDF report. **No per-test view code.**
New families just pick a `kind` + `chart.type`:

| Instrument | `kind`    | `chart.type`        | notes                                   |
|------------|-----------|---------------------|-----------------------------------------|
| MMPI       | `profile` | `line`              | T-score bands, validity vs clinical     |
| MBTI       | `type`    | `bar` (or none)     | forced-choice items, 4 dichotomies      |
| NEO        | `profile` | `radar` / `bar`     | facets rolled into domains              |
| Gardner    | `themes`  | `radar`             | ranked intelligences                    |
| Strong     | `themes`  | `bar`               | interest themes + matches               |
| Wellbeing  | `index`   | `gauge`             | single summed index (the example here)  |

`severity` on items/blocks encodes meaning, not decoration: `ok` / `caution` / `severe` / `flag` drive the
colours the renderer uses.

---

## How plugins are discovered

`registry.py` walks the `engine.instruments` package with `pkgutil.iter_modules`, imports each **subpackage**,
and reads its module-level `INSTRUMENT` instance. They're indexed by `(slug, version)` plus a
`latest-per-slug` map, so callers can pin a version or get the newest. Discovery runs once at import (the
engine is stateless — the registry is rebuilt every process start).

```python
# engine/instruments/mmpi/__init__.py
from engine.instruments.mmpi.plugin import MMPIInstrument
INSTRUMENT = MMPIInstrument()
```

---

## API

| Method & path                               | Returns                                            |
|---------------------------------------------|----------------------------------------------------|
| `GET /healthz`                              | `{status, instruments}`                            |
| `GET /instruments`                          | `[{slug, version, title, kind}]` (latest per slug) |
| `GET /instruments/{slug}/schema?version=`   | `{meta, schema}` (InstrumentMeta + QuestionSchema) |
| `POST /score`                               | `{slug, version, score_result, result_view}`       |

`POST /score` body: `{slug, version?, responses, demographics}` — `version` defaults to the latest.
Errors are uniform JSON `{error: "..."}` with `404` (unknown instrument) or `422` (bad responses /
missing demographics).

---

## How to add a new instrument

1. **Create a subpackage** `engine/instruments/<your_slug>/` with `plugin.py` + `__init__.py`.
2. **Implement the four methods** in a class (set `slug` and `version`):
   - `metadata()` → `InstrumentMeta(kind=..., demographics=[...], page_size=...)`.
   - `question_schema()` → `QuestionSchema(items=[QuestionItem(...)])`.
   - `score(responses, demographics)` → `ScoreResult(raw=..., derived=...)` — **any logic you need**.
     Raise `EngineError(msg, status=422)` on bad input.
   - `build_result(score)` → `ResultView(kind=..., items=[...], interpretation=[...], chart=ChartSpec(...))`.
3. **Export the instance**: in `__init__.py`, `INSTRUMENT = YourInstrument()`. That's the whole registration —
   the registry finds it automatically; **no engine-core or API change**.
4. **Bundle data with the plugin** if it has any (e.g. a JSON definition) and declare it in
   `pyproject.toml` under `[tool.setuptools.package-data]` so it ships in the image.
5. **Write tests** under `tests/` (unit-test the calculation + the ResultView). If you're cloning a verified
   reference implementation, add an **equivalence test** against a frozen copy of that reference, like
   MMPI's `test_equivalence.py` (a release blocker).

### Sketch: a forced-choice instrument (future MBTI / Strong)

Forced-choice tests don't have option *weights* summed into scales; each item picks between poles. The
plugin's internals differ, the contract doesn't:

```python
class MBTIInstrument:
    slug, version = "mbti", 1

    def metadata(self):
        return InstrumentMeta(slug=self.slug, title="...", version=1, kind="type",
                              demographics=[])

    def question_schema(self):
        # each item offers two (or more) statements; option.value encodes the pole it favours, e.g. "E"/"I"
        return QuestionSchema(items=[
            QuestionItem(id=1, text="...", options=[OptionSpec(value="E", label="..."),
                                                    OptionSpec(value="I", label="...")]),
            # ...
        ])

    def score(self, responses, demographics):
        # tally poles per dichotomy, then pick the dominant letter on each axis
        tally = {"E": 0, "I": 0, "S": 0, "N": 0, "T": 0, "F": 0, "J": 0, "P": 0}
        for qid, pole in responses.items():
            tally[pole] += 1
        type_code = "".join([
            "E" if tally["E"] >= tally["I"] else "I",
            "S" if tally["S"] >= tally["N"] else "N",
            "T" if tally["T"] >= tally["F"] else "F",
            "J" if tally["J"] >= tally["P"] else "P",
        ])
        return ScoreResult(raw=tally, derived={"type": type_code})

    def build_result(self, score):
        code = score.derived["type"]
        return ResultView(
            kind="type",
            summary=f"تیپ شخصیتی: {code}",
            items=[ResultItem(key="type", label="Type", value=code)],
            interpretation=[InterpretationBlock(title=code, body="...")],
            chart=ChartSpec(type="bar", labels=["E-I", "S-N", "T-F", "J-P"],
                            series=[ChartSeries(name="strength", data=[...])]),
            meta={"slug": self.slug, "version": self.version},
        )
```

Strong (interest themes) is similar but `kind="themes"`: `score()` ranks RIASEC themes, `build_result()`
emits the ranked themes as `items` + a `radar`/`bar` chart and a list of best-matching profiles.

---

## Running

- Tests (incl. the MMPI equivalence release blocker):
  `docker compose run --rm engine pytest`  ·  or locally: `cd engine && pip install -e ".[test]" && pytest`
- Serve (internal only): `docker compose up engine` → `GET /healthz`.
