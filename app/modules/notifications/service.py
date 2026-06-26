"""Notifications service — the enqueue seam other modules call.

Identity calls ``send_otp_sms`` (never the Celery task directly), keeping the
module boundary at ``service.py``. The web tier only enqueues; the worker sends.
"""

from __future__ import annotations

from app.modules.notifications.tasks import send_otp


def send_otp_sms(phone: str, code: str, purpose: str = "login") -> None:
    """Enqueue an OTP SMS on the `notifications` queue (runs inline under
    Celery eager mode in tests)."""
    send_otp.apply_async(args=[phone, code, purpose], queue="notifications")
