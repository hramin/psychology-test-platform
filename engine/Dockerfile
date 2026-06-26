# The Scoring Engine — its own image, separate from the app. Build context is
# this directory (engine/), so the engine is fully self-contained: it bundles its
# plugins (incl. the MMPI definition) and depends on nothing from the app.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv

WORKDIR /srv

# Source first (the build needs it to install the package + bundled data), then
# install dependencies and the package itself (with test extras so the image can
# run `pytest`, including the equivalence test).
COPY pyproject.toml /srv/pyproject.toml
COPY engine/ /srv/engine/
COPY tests/ /srv/tests/
COPY ENGINE.md /srv/ENGINE.md
RUN pip install --upgrade pip && pip install ".[test]"

# Internal-only service port (never published to the host; see docker-compose.yml)
EXPOSE 9000
CMD ["gunicorn", "engine.app:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:9000"]
