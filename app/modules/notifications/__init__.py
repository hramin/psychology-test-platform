"""Notifications module: SMS/OTP delivery via the Celery `notifications` worker.

Slow third parties (Kavenegar) run in the worker, never on the request path.
Other modules enqueue through ``notifications.service`` only.
"""
