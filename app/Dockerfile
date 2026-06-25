# One app image — the web tier (and future Celery workers) share it.
# Build context is the repo ROOT (see docker-compose.yml) so the seeder's
# mmpi_v1.json and the tests/ tree are available to copy in.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/srv

WORKDIR /srv

# Dependencies first (better layer caching)
COPY app/requirements.txt /srv/app/requirements.txt
RUN pip install --upgrade pip && pip install -r /srv/app/requirements.txt

# Application code + the verified test definition + the test suite
COPY app/ /srv/app/
COPY mmpi_v1.json /srv/mmpi_v1.json
COPY pytest.ini /srv/pytest.ini
COPY tests/ /srv/tests/

RUN chmod +x /srv/app/entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/srv/app/entrypoint.sh"]
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "2", "-b", "0.0.0.0:8000"]
