from __future__ import annotations

import hashlib
import hmac
import json
import time
import unittest

from fulfilment.config import Settings
from fulfilment.health import health
from fulfilment.orders import (
    AmountMismatchError,
    InMemoryOrderStore,
    ProductConflictError,
    ProductMappingError,
    process_stripe_event,
)
from fulfilment.stripe_webhook import StripeSignatureError, construct_event


WEBHOOK_SECRET = "whsec_test_secret"


def settings() -> Settings:
    return Settings(
        stripe_webhook_secret=WEBHOOK_SECRET,
        stripe_secret_key="sk_test_placeholder",
        google_cloud_project="test-project",
        full_analysis_price_id="price_full_test",
        expert_review_price_id="price_expert_test",
        full_analysis_product_id="prod_full_test",
        expert_review_product_id="prod_expert_test",
    )


def session(
    *,
    session_id: str = "cs_test_123",
    price_id: str = "price_full_test",
    product_id: str = "prod_full_test",
    amount_total: int = 3900,
    currency: str = "aud",
    payment_status: str = "paid",
    customer_name: str | None = "Jane Owner",
    customer_email: str | None = "jane@example.com",
    metadata_code: str | None = "FULL_ANALYSIS",
) -> dict:
    return {
        "id": session_id,
        "object": "checkout.session",
        "payment_status": payment_status,
        "payment_intent": "pi_test_123",
        "customer": "cus_test_123",
        "amount_total": amount_total,
        "currency": currency,
        "customer_details": {
            "name": customer_name,
            "email": customer_email,
        },
        "metadata": {"senalo_product_code": metadata_code} if metadata_code else {},
        "line_items": {
            "data": [
                {
                    "price": {
                        "id": price_id,
                        "product": product_id,
                    }
                }
            ]
        },
    }


def event(event_id: str, event_type: str, checkout_session: dict) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "data": {"object": checkout_session},
    }


def signed_payload(payload: dict, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    timestamp = int(time.time())
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + raw,
        hashlib.sha256,
    ).hexdigest()
    return raw, f"t={timestamp},v1={signature}"


class Gate2WebhookTests(unittest.TestCase):
    def test_health_endpoint_payload(self) -> None:
        self.assertEqual(health(), {"status": "healthy"})

    def test_valid_paid_full_analysis_creates_paid_order(self) -> None:
        store = InMemoryOrderStore()
        result = process_stripe_event(
            event("evt_full", "checkout.session.completed", session()),
            store,
            settings(),
        )
        self.assertEqual(result["status"], "processed")
        self.assertEqual(result["product_code"], "FULL_ANALYSIS")
        self.assertEqual(result["payment_status"], "PAID")
        self.assertEqual(len(store.orders), 1)
        order = next(iter(store.orders.values()))
        self.assertEqual(order.fulfilment_status, "NOT_STARTED")

    def test_valid_paid_expert_review_creates_paid_order(self) -> None:
        store = InMemoryOrderStore()
        result = process_stripe_event(
            event(
                "evt_expert",
                "checkout.session.completed",
                session(
                    session_id="cs_test_expert",
                    price_id="price_expert_test",
                    product_id="prod_expert_test",
                    amount_total=14900,
                    metadata_code="EXPERT_REVIEW",
                ),
            ),
            store,
            settings(),
        )
        self.assertEqual(result["product_code"], "EXPERT_REVIEW")
        self.assertEqual(result["payment_status"], "PAID")

    def test_invalid_signature_rejected(self) -> None:
        raw, header = signed_payload(event("evt_bad_sig", "checkout.session.completed", session()))
        with self.assertRaises(StripeSignatureError):
            construct_event(raw, header.replace("v1=", "v1=bad"), WEBHOOK_SECRET)

    def test_duplicate_event_creates_one_order_only(self) -> None:
        store = InMemoryOrderStore()
        payload = event("evt_duplicate", "checkout.session.completed", session())
        first = process_stripe_event(payload, store, settings())
        second = process_stripe_event(payload, store, settings())
        self.assertEqual(first["status"], "processed")
        self.assertEqual(second["status"], "duplicate_ignored")
        self.assertEqual(len(store.orders), 1)

    def test_pending_completed_session_does_not_mark_paid(self) -> None:
        store = InMemoryOrderStore()
        result = process_stripe_event(
            event(
                "evt_pending",
                "checkout.session.completed",
                session(payment_status="unpaid"),
            ),
            store,
            settings(),
        )
        self.assertEqual(result["payment_status"], "PENDING")
        order = next(iter(store.orders.values()))
        self.assertIsNone(order.paid_at)

    def test_async_payment_succeeded_marks_paid(self) -> None:
        store = InMemoryOrderStore()
        result = process_stripe_event(
            event(
                "evt_async_success",
                "checkout.session.async_payment_succeeded",
                session(payment_status="unpaid"),
            ),
            store,
            settings(),
        )
        self.assertEqual(result["payment_status"], "PAID")

    def test_async_payment_failed_marks_failed(self) -> None:
        store = InMemoryOrderStore()
        result = process_stripe_event(
            event(
                "evt_async_failed",
                "checkout.session.async_payment_failed",
                session(payment_status="unpaid"),
            ),
            store,
            settings(),
        )
        self.assertEqual(result["payment_status"], "FAILED")

    def test_unknown_metadata_fails_safely(self) -> None:
        store = InMemoryOrderStore()
        with self.assertRaises(ProductMappingError):
            process_stripe_event(
                event(
                    "evt_unknown_metadata",
                    "checkout.session.completed",
                    session(metadata_code="UNKNOWN_PRODUCT"),
                ),
                store,
                settings(),
            )
        self.assertEqual(len(store.orders), 0)
        self.assertEqual(store.events["evt_unknown_metadata"].processing_status, "FAILED")

    def test_unknown_price_id_fails_safely(self) -> None:
        store = InMemoryOrderStore()
        with self.assertRaises(ProductConflictError):
            process_stripe_event(
                event(
                    "evt_unknown_price",
                    "checkout.session.completed",
                    session(price_id="price_unknown"),
                ),
                store,
                settings(),
            )
        self.assertEqual(len(store.orders), 0)
        self.assertEqual(store.events["evt_unknown_price"].error_code, "PRODUCT_CONFLICT")

    def test_metadata_price_conflict_fails_safely(self) -> None:
        store = InMemoryOrderStore()
        with self.assertRaises(ProductConflictError):
            process_stripe_event(
                event(
                    "evt_metadata_price_conflict",
                    "checkout.session.completed",
                    session(
                        price_id="price_expert_test",
                        product_id="prod_expert_test",
                        amount_total=3900,
                        metadata_code="FULL_ANALYSIS",
                    ),
                ),
                store,
                settings(),
            )
        self.assertEqual(len(store.orders), 0)
        self.assertEqual(store.events["evt_metadata_price_conflict"].error_code, "PRODUCT_CONFLICT")

    def test_amount_mismatch_fails_safely(self) -> None:
        store = InMemoryOrderStore()
        with self.assertRaises(AmountMismatchError):
            process_stripe_event(
                event(
                    "evt_amount_mismatch",
                    "checkout.session.completed",
                    session(amount_total=14900),
                ),
                store,
                settings(),
            )
        self.assertEqual(len(store.orders), 0)
        self.assertEqual(store.events["evt_amount_mismatch"].error_code, "AMOUNT_MISMATCH")

    def test_missing_customer_name_is_flagged(self) -> None:
        store = InMemoryOrderStore()
        process_stripe_event(
            event(
                "evt_missing_name",
                "checkout.session.completed",
                session(customer_name=None),
            ),
            store,
            settings(),
        )
        order = next(iter(store.orders.values()))
        self.assertIn("MISSING_CUSTOMER_NAME", order.customer_data_flags)

    def test_missing_customer_email_is_flagged(self) -> None:
        store = InMemoryOrderStore()
        process_stripe_event(
            event(
                "evt_missing_email",
                "checkout.session.completed",
                session(customer_email=None),
            ),
            store,
            settings(),
        )
        order = next(iter(store.orders.values()))
        self.assertIn("MISSING_CUSTOMER_EMAIL", order.customer_data_flags)

    def test_failed_event_can_be_replayed_after_configuration_fix(self) -> None:
        store = InMemoryOrderStore()
        payload = event(
            "evt_replay_failed",
            "checkout.session.completed",
            session(metadata_code="UNKNOWN_PRODUCT"),
        )
        with self.assertRaises(ProductMappingError):
            process_stripe_event(payload, store, settings())
        fixed_payload = event(
            "evt_replay_failed",
            "checkout.session.completed",
            session(metadata_code="FULL_ANALYSIS"),
        )
        result = process_stripe_event(fixed_payload, store, settings())
        self.assertEqual(result["status"], "processed")
        self.assertEqual(len(store.orders), 1)

    def test_valid_signature_constructs_event_from_raw_body(self) -> None:
        payload = event("evt_signed", "checkout.session.completed", session())
        raw, header = signed_payload(payload)
        constructed = construct_event(raw, header, WEBHOOK_SECRET)
        self.assertEqual(constructed["id"], "evt_signed")

    def test_session_metadata_without_line_items_maps_product(self) -> None:
        store = InMemoryOrderStore()
        checkout_session = session()
        checkout_session.pop("line_items")
        result = process_stripe_event(
            event("evt_metadata_only", "checkout.session.completed", checkout_session),
            store,
            settings(),
        )
        order = next(iter(store.orders.values()))
        self.assertEqual(result["product_code"], "FULL_ANALYSIS")
        self.assertIsNone(order.stripe_price_id)
        self.assertIsNone(order.stripe_product_id)

    def test_missing_metadata_fails_even_if_price_exists(self) -> None:
        store = InMemoryOrderStore()
        with self.assertRaises(ProductMappingError):
            process_stripe_event(
                event(
                    "evt_missing_metadata",
                    "checkout.session.completed",
                    session(metadata_code=None),
                ),
                store,
                settings(),
            )
        self.assertEqual(len(store.orders), 0)

    def test_async_payment_success_updates_existing_pending_order(self) -> None:
        store = InMemoryOrderStore()
        completed = event(
            "evt_pending_then_success",
            "checkout.session.completed",
            session(payment_status="unpaid"),
        )
        async_success = event(
            "evt_pending_then_success_async",
            "checkout.session.async_payment_succeeded",
            session(payment_status="unpaid"),
        )
        first = process_stripe_event(completed, store, settings())
        second = process_stripe_event(async_success, store, settings())
        self.assertEqual(first["payment_status"], "PENDING")
        self.assertEqual(second["payment_status"], "PAID")
        self.assertEqual(len(store.orders), 1)
        order = next(iter(store.orders.values()))
        self.assertEqual(order.payment_status, "PAID")

    def test_async_payment_failed_updates_existing_pending_order(self) -> None:
        store = InMemoryOrderStore()
        completed = event(
            "evt_pending_then_failed",
            "checkout.session.completed",
            session(payment_status="unpaid"),
        )
        async_failed = event(
            "evt_pending_then_failed_async",
            "checkout.session.async_payment_failed",
            session(payment_status="unpaid"),
        )
        first = process_stripe_event(completed, store, settings())
        second = process_stripe_event(async_failed, store, settings())
        self.assertEqual(first["payment_status"], "PENDING")
        self.assertEqual(second["payment_status"], "FAILED")
        self.assertEqual(len(store.orders), 1)
        order = next(iter(store.orders.values()))
        self.assertEqual(order.payment_status, "FAILED")

    def test_unsupported_event_type_is_ignored_successfully(self) -> None:
        store = InMemoryOrderStore()
        result = process_stripe_event(
            {"id": "evt_other", "type": "customer.created", "data": {"object": {}}},
            store,
            settings(),
        )
        self.assertEqual(result["status"], "ignored")
        self.assertEqual(len(store.orders), 0)
        self.assertEqual(store.events["evt_other"].processing_status, "PROCESSED")


if __name__ == "__main__":
    unittest.main()
