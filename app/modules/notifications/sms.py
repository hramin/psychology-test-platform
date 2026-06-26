"""Pluggable SMS backends.

``mock`` (the default in dev/test) just **logs** the code and records it in an
``outbox`` so the whole OTP flow is exercisable without Kavenegar credentials.
``kavenegar`` sends for real — defaulting to ``sms_send`` (plain message, as in
the provided example), or ``verify_lookup`` (a pre-approved OTP template) when
``KAVENEGAR_USE_TEMPLATE`` is set. The Kavenegar SDK is **imported lazily** so the
mock path (and the test suite) never needs the package installed.
"""

from __future__ import annotations

import logging
from typing import Protocol

from app.config import settings

log = logging.getLogger("notifications.sms")


class SmsBackend(Protocol):
    def send_otp(self, receptor: str, code: str, purpose: str) -> None: ...


class MockSmsBackend:
    """Logs the OTP and records it in ``outbox`` (for local dev + tests)."""

    outbox: list[dict] = []

    def send_otp(self, receptor: str, code: str, purpose: str) -> None:
        message = settings.otp_message_template.format(code=code)
        self.outbox.append(
            {"receptor": receptor, "code": code, "purpose": purpose, "message": message}
        )
        log.warning("[mock-sms] to=%s purpose=%s :: %s", receptor, purpose, message)


class KavenegarSmsBackend:
    """Sends via Kavenegar. SDK is imported lazily inside ``send_otp``."""

    def send_otp(self, receptor: str, code: str, purpose: str) -> None:
        from kavenegar import APIException, HTTPException, KavenegarAPI

        api = KavenegarAPI(settings.kavenegar_api_key)
        try:
            if settings.kavenegar_use_template:
                api.verify_lookup(
                    {
                        "receptor": receptor,
                        "template": settings.kavenegar_otp_template,
                        "token": code,
                        "type": "sms",
                    }
                )
            else:
                api.sms_send(
                    {
                        "sender": settings.kavenegar_sender,
                        "receptor": receptor,
                        "message": settings.otp_message_template.format(code=code),
                    }
                )
        except (APIException, HTTPException):
            log.exception("kavenegar send failed for %s", receptor)
            raise


def get_backend() -> SmsBackend:
    if settings.sms_backend == "kavenegar":
        return KavenegarSmsBackend()
    return MockSmsBackend()
