from __future__ import annotations

from typing import Any

import stripe
from stripe import SignatureVerificationError


class StripeSignatureError(Exception):
    pass


def construct_event(
    raw_body: bytes,
    signature_header: str | None,
    webhook_secret: str,
    *,
    tolerance_seconds: int = 300,
) -> dict[str, Any]:
    if not webhook_secret:
        raise StripeSignatureError("Webhook secret is not configured")
    if not signature_header:
        raise StripeSignatureError("Stripe-Signature header is missing")
    try:
        event = stripe.Webhook.construct_event(
            raw_body,
            signature_header,
            webhook_secret,
            tolerance=tolerance_seconds,
        )
    except (ValueError, SignatureVerificationError) as exc:
        raise StripeSignatureError("Stripe webhook verification failed") from exc
    return event.to_dict()
