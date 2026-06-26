"""Celery tasks for the `notifications` queue.

The web tier only *enqueues* these (via ``notifications.service``); the actual
Kavenegar round-trip happens here, in the worker, off the request path.
"""

from __future__ import annotations

from app.celery_app import celery_app
from app.modules.notifications.sms import get_backend


@celery_app.task(name="notifications.send_otp")
def send_otp(phone: str, code: str, purpose: str = "login") -> None:
    get_backend().send_otp(phone, code, purpose)
