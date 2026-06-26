"""The Scoring Engine FastAPI service.

Stateless and internal: it loads instrument plugins at import (via
:mod:`engine.registry`) and exposes a small API used by the app's ``EngineClient``
(Phase 5) and, later, by the B2B gateway. It has **no database and no dependency
on the app**.

Endpoints:
  * ``GET  /healthz``                          — liveness + plugin count
  * ``GET  /instruments``                      — [{slug, version, title, kind}]
  * ``GET  /instruments/{slug}/schema?version=`` — InstrumentMeta + QuestionSchema
  * ``POST /score``                            — {slug, version?, responses,
        demographics} -> {score_result, result_view}
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from engine import registry
from engine.contracts import (
    EngineError,
    SchemaOut,
    ScoreRequest,
    ScoreResponse,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Scoring Engine", version="0.1.0")

    @app.exception_handler(EngineError)
    async def _engine_error(_request, exc: EngineError):
        return JSONResponse({"error": exc.message}, status_code=exc.status)

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "instruments": len(registry.all_instruments())}

    @app.get("/instruments")
    async def instruments():
        return registry.list_summaries()

    @app.get("/instruments/{slug}/schema", response_model=SchemaOut,
             response_model_by_alias=True)
    async def schema(slug: str, version: int | None = None):
        inst = registry.get(slug, version)
        return SchemaOut(meta=inst.metadata(), schema=inst.question_schema())

    @app.post("/score", response_model=ScoreResponse)
    async def score(req: ScoreRequest):
        inst = registry.get(req.slug, req.version)
        try:
            result = inst.score(req.responses, req.demographics)
        except ValueError as exc:  # plugin-raised input error
            raise EngineError(str(exc), status=422) from exc
        view = inst.build_result(result)
        return ScoreResponse(
            slug=inst.slug,
            version=inst.version,
            score_result=result,
            result_view=view,
        )

    return app


app = create_app()
