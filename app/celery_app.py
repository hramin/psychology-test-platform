"""Celery application — the async task tier.

Per CLAUDE.md, every slow third party runs in a worker: the web tier only does
fast Postgres/Redis work and **enqueues**. In Phase 2 the single task is OTP SMS
send (Kavenegar), routed to the ``notifications`` queue.

Broker and result backend default to the Redis URL. ``task_always_eager`` (driven
by settings) makes tasks run inline — used by the test suite so no worker/broker
is needed.
"""

from __future__ import annotations

from celery import Celery

from app.config import settings

celery_app = Celery("psychology_test_platform")

celery_app.conf.update(
    broker_url=settings.celery_broker_url or settings.redis_url,
    result_backend=settings.celery_result_backend or settings.redis_url,
    task_default_queue="default",
    # Route every notifications task to the dedicated `notifications` queue.
    task_routes={"notifications.*": {"queue": "notifications"}},
    task_always_eager=settings.celery_task_always_eager,
    task_eager_propagates=True,
    timezone="UTC",
    enable_utc=True,
)

# Import task modules so their @task definitions register on the app.
celery_app.autodiscover_tasks(["app.modules.notifications"])
