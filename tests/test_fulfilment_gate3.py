from __future__ import annotations

import logging
import re
import unittest
from dataclasses import replace
from datetime import timedelta

from fulfilment import app as app_module
from fulfilment.config import Settings
from fulfilment.email_service import RecordingEmailProvider
from fulfilment.fulfilment_service import OrderFulfilmentService
from fulfilment.models import utc_now
from fulfilment.orders import InMemoryOrderStore, process_stripe_event
from fulfilment.tokens import (
    attach_new_token,
    build_secure_upload_url,
    derive_raw_token,
    hash_token,
    reissue_token,
    validate_token,
    TokenValidationError,
)
from tests.test_fulfilment_gate2 import event, session


def settings() -> Settings:
    return Settings(
        stripe_webhook_secret="whsec_test_secret",
        stripe_secret_key="sk_test_placeholder",
        google_cloud_project="test-project",
        full_analysis_price_id="price_full_test",
        expert_review_price_id="price_expert_test",
        full_analysis_product_id="prod_full_test",
        expert_review_product_id="prod_expert_test",
        resend_api_key="re_test_placeholder",
        senalo_email_from="SENALO <notifications@example.test>",
        senalo_email_reply_to="hello@senalo.com.au",
        senalo_public_fulfilment_base_url="https://fulfilment.example.test",
        token_expiry_days=14,
        token_derivation_secret="test_derivation_secret_32_bytes_minimum",
    )


def token_from_message_body(body: str) -> str:
    match = re.search(r"https://fulfilment\.example\.test/upload\?t=([A-Za-z0-9_-]+)", body)
    if not match:
        raise AssertionError("secure upload token URL was not found")
    return match.group(1)


class Gate3FulfilmentTests(unittest.TestCase):
    def test_paid_full_analysis_creates_token_sends_email_once_and_awaits_upload(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)

        result = process_stripe_event(
            event("evt_full_gate3", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=service,
        )

        self.assertEqual(result["fulfilment_status"], "AWAITING_UPLOAD")
        order = next(iter(store.orders.values()))
        self.assertEqual(order.email_status, "SENT")
        self.assertEqual(order.email_attempt_count, 1)
        self.assertEqual(len(email_provider.sent), 1)
        self.assertIsNotNone(order.token_hash)
        message, _ = email_provider.sent[0]
        raw_token = token_from_message_body(message.body)
        self.assertNotEqual(order.token_hash, raw_token)
        self.assertNotIn(raw_token, str(order.to_dict()))
        self.assertIsNotNone(order.token_seed)
        self.assertIn("SENALO Full Analysis", message.subject)
        self.assertIn("12-month forecast", message.body)
        self.assertIn("Base / Downside / Upside scenarios", message.body)
        self.assertIn("PDF report", message.body)
        self.assertIn("Excel analysis", message.body)

    def test_paid_expert_review_uses_expert_template_and_secure_link(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)

        process_stripe_event(
            event(
                "evt_expert_gate3",
                "checkout.session.completed",
                session(
                    session_id="cs_expert_gate3",
                    price_id="price_expert_test",
                    product_id="prod_expert_test",
                    amount_total=14900,
                    metadata_code="EXPERT_REVIEW",
                ),
            ),
            store,
            settings(),
            fulfilment_service=service,
        )

        order = next(iter(store.orders.values()))
        message, _ = email_provider.sent[0]
        self.assertEqual(order.fulfilment_status, "AWAITING_UPLOAD")
        self.assertIn("SENALO Expert Review", message.subject)
        self.assertIn("manual review", message.body)
        self.assertIn("customised commentary", message.body)
        self.assertIn("3–5 prioritised management actions", message.body)
        self.assertIn("https://fulfilment.example.test/upload?t=", message.body)

    def test_duplicate_webhook_event_does_not_send_duplicate_email(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        payload = event("evt_duplicate_gate3", "checkout.session.completed", session())

        first = process_stripe_event(payload, store, settings(), fulfilment_service=service)
        second = process_stripe_event(payload, store, settings(), fulfilment_service=service)

        self.assertEqual(first["status"], "processed")
        self.assertEqual(second["status"], "duplicate_ignored")
        self.assertEqual(len(email_provider.sent), 1)

    def test_same_order_processing_twice_does_not_resend_when_sent(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)

        process_stripe_event(
            event("evt_same_order_1", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=service,
        )
        process_stripe_event(
            event("evt_same_order_2", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=service,
        )

        self.assertEqual(len(store.orders), 1)
        self.assertEqual(len(email_provider.sent), 1)

    def test_token_hash_validation_succeeds_and_wrong_token_fails(self) -> None:
        store = InMemoryOrderStore()
        order, raw_token = attach_new_token(
            next(
                iter(
                    self._paid_order_store(
                        store,
                        RecordingEmailProvider(),
                        "evt_validate_token",
                    ).orders.values()
                )
            )
            ,
            derivation_secret=settings().token_derivation_secret,
        )
        store.save_order(order)
        self.assertEqual(
            validate_token(raw_token, store, derivation_secret=settings().token_derivation_secret).order_id,
            order.order_id,
        )
        with self.assertRaises(TokenValidationError):
            validate_token(
                "wrong_token_value_that_is_long_enough",
                store,
                derivation_secret=settings().token_derivation_secret,
            )

    def test_expired_and_revoked_tokens_fail(self) -> None:
        store = InMemoryOrderStore()
        order = next(iter(self._paid_order_store(store, RecordingEmailProvider(), "evt_token_state").orders.values()))

        with_token, raw_token = attach_new_token(
            order,
            derivation_secret=settings().token_derivation_secret,
        )
        expired = replace(with_token, token_expires_at=utc_now() - timedelta(seconds=1))
        store.save_order(expired)
        with self.assertRaises(TokenValidationError):
            validate_token(raw_token, store, derivation_secret=settings().token_derivation_secret)

        active, raw_token = attach_new_token(
            order,
            derivation_secret=settings().token_derivation_secret,
        )
        revoked = replace(active, token_revoked_at=utc_now())
        store.save_order(revoked)
        with self.assertRaises(TokenValidationError):
            validate_token(raw_token, store, derivation_secret=settings().token_derivation_secret)

    def test_token_reissue_invalidates_old_token_and_validates_new_token(self) -> None:
        store = InMemoryOrderStore()
        order = next(iter(self._paid_order_store(store, RecordingEmailProvider(), "evt_reissue").orders.values()))
        old_hash = order.token_hash
        old_order, old_raw = attach_new_token(
            order,
            derivation_secret=settings().token_derivation_secret,
        )
        store.save_order(old_order)

        new_order, new_raw = reissue_token(
            order.order_id,
            store,
            derivation_secret=settings().token_derivation_secret,
        )

        self.assertNotEqual(old_hash, new_order.token_hash)
        with self.assertRaises(TokenValidationError):
            validate_token(old_raw, store, derivation_secret=settings().token_derivation_secret)
        self.assertEqual(
            validate_token(new_raw, store, derivation_secret=settings().token_derivation_secret).order_id,
            order.order_id,
        )

    def test_same_order_token_can_be_reproduced_without_raw_token_storage(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        process_stripe_event(
            event("evt_reproduce_token", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=service,
        )
        order = next(iter(store.orders.values()))
        first_token = token_from_message_body(email_provider.sent[0][0].body)
        reproduced = derive_raw_token(
            order.token_seed or "",
            order.token_version,
            settings().token_derivation_secret,
        )
        self.assertEqual(first_token, reproduced)
        self.assertNotIn(first_token, str(order.to_dict()))

    def test_email_retry_reuses_same_url_key_and_does_not_reissue_token(self) -> None:
        store = InMemoryOrderStore()
        ambiguous_provider = RecordingThenFailProvider("PROVIDER_TIMEOUT_UNKNOWN")
        service = OrderFulfilmentService(store, settings(), ambiguous_provider)
        process_stripe_event(
            event("evt_retry_same_payload", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=service,
        )
        failed_order = next(iter(store.orders.values()))
        first_url = token_from_message_body(ambiguous_provider.sent[0][0].body)
        first_key = ambiguous_provider.sent[0][1]
        first_seed = failed_order.token_seed
        first_version = failed_order.token_version
        self.assertEqual(failed_order.email_status, "FAILED")

        retry_provider = RecordingEmailProvider()
        retry_service = OrderFulfilmentService(store, settings(), retry_provider)
        retried = retry_service.retry_failed_email(failed_order.order_id)
        retry_url = token_from_message_body(retry_provider.sent[0][0].body)
        retry_key = retry_provider.sent[0][1]

        self.assertEqual(first_url, retry_url)
        self.assertEqual(first_key, retry_key)
        self.assertEqual(retried.token_seed, first_seed)
        self.assertEqual(retried.token_version, first_version)
        self.assertIsNone(retried.token_revoked_at)
        self.assertEqual(retried.email_attempt_count, 2)

    def test_different_orders_and_seed_changes_produce_different_tokens(self) -> None:
        store_a = InMemoryOrderStore()
        provider_a = RecordingEmailProvider()
        self._paid_order_store(store_a, provider_a, "evt_order_a")
        token_a = token_from_message_body(provider_a.sent[0][0].body)

        store_b = InMemoryOrderStore()
        provider_b = RecordingEmailProvider()
        self._paid_order_store(store_b, provider_b, "evt_order_b")
        token_b = token_from_message_body(provider_b.sent[0][0].body)
        self.assertNotEqual(token_a, token_b)

        changed_seed_token = derive_raw_token(
            "different_seed_value",
            1,
            settings().token_derivation_secret,
        )
        self.assertNotEqual(token_a, changed_seed_token)

    def test_missing_or_short_token_derivation_secret_fails_safely(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        bad_settings = replace(settings(), token_derivation_secret=None)
        service = OrderFulfilmentService(store, bad_settings, email_provider)
        process_stripe_event(
            event("evt_missing_secret", "checkout.session.completed", session()),
            store,
            bad_settings,
            fulfilment_service=service,
        )
        order = next(iter(store.orders.values()))
        self.assertEqual(order.email_status, "FAILED")
        self.assertEqual(order.email_last_error, "TOKEN_DERIVATION_SECRET_INVALID")
        self.assertIsNone(order.token_hash)
        self.assertEqual(len(email_provider.sent), 0)

    def test_secure_url_contains_token_but_not_order_id(self) -> None:
        url = build_secure_upload_url("https://fulfilment.example.test", "opaque_token")
        self.assertEqual(url, "https://fulfilment.example.test/upload?t=opaque_token")
        self.assertNotIn("order_id", url)

    def test_raw_token_not_persisted_or_logged(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        with self.assertLogs("senalo.fulfilment", level=logging.INFO) as captured:
            process_stripe_event(
                event("evt_no_token_logs", "checkout.session.completed", session()),
                store,
                settings(),
                fulfilment_service=service,
            )
        raw_token = token_from_message_body(email_provider.sent[0][0].body)
        self.assertNotIn(raw_token, str(next(iter(store.orders.values())).to_dict()))
        self.assertNotIn(settings().token_derivation_secret or "", "\n".join(captured.output))
        self.assertNotIn(raw_token, "\n".join(captured.output))

    def test_email_provider_failure_keeps_recoverable_order_and_retry_succeeds(self) -> None:
        store = InMemoryOrderStore()
        failing_provider = RecordingEmailProvider(fail_with="RESEND_SEND_FAILED")
        failing_service = OrderFulfilmentService(store, settings(), failing_provider)
        process_stripe_event(
            event("evt_email_failure", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=failing_service,
        )
        order = next(iter(store.orders.values()))
        self.assertEqual(order.email_status, "FAILED")
        self.assertEqual(order.email_attempt_count, 1)
        self.assertEqual(order.fulfilment_status, "NOT_STARTED")

        retry_provider = RecordingEmailProvider()
        retry_service = OrderFulfilmentService(store, settings(), retry_provider)
        retried = retry_service.retry_failed_email(order.order_id)
        self.assertEqual(retried.email_status, "SENT")
        self.assertEqual(retried.email_attempt_count, 2)
        self.assertEqual(retried.fulfilment_status, "AWAITING_UPLOAD")
        self.assertEqual(len(retry_provider.sent), 1)

    def test_missing_customer_name_uses_generic_greeting(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        process_stripe_event(
            event(
                "evt_missing_name_gate3",
                "checkout.session.completed",
                session(customer_name=None),
            ),
            store,
            settings(),
            fulfilment_service=service,
        )
        self.assertTrue(email_provider.sent[0][0].body.startswith("Hi,"))

    def test_missing_customer_email_does_not_attempt_send_and_marks_failed(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        process_stripe_event(
            event(
                "evt_missing_email_gate3",
                "checkout.session.completed",
                session(customer_email=None),
            ),
            store,
            settings(),
            fulfilment_service=service,
        )
        order = next(iter(store.orders.values()))
        self.assertEqual(len(email_provider.sent), 0)
        self.assertEqual(order.email_status, "FAILED")
        self.assertEqual(order.email_last_error, "MISSING_CUSTOMER_EMAIL")

    def test_unknown_product_code_sends_no_email(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        with self.assertRaises(Exception):
            process_stripe_event(
                event(
                    "evt_unknown_product_gate3",
                    "checkout.session.completed",
                    session(metadata_code="UNKNOWN"),
                ),
                store,
                settings(),
                fulfilment_service=service,
            )
        self.assertEqual(len(email_provider.sent), 0)

    def test_upload_placeholder_valid_invalid_and_security_headers(self) -> None:
        store = InMemoryOrderStore()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        process_stripe_event(
            event("evt_upload_route", "checkout.session.completed", session()),
            store,
            settings(),
            fulfilment_service=service,
        )
        raw_token = token_from_message_body(email_provider.sent[0][0].body)

        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        try:
            valid = app_module.upload_form(DummyRequest(raw_token))
            invalid = app_module.upload_form(DummyRequest("wrong_token_value_that_is_long_enough"))
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings

        self.assertEqual(valid.status_code, 200)
        self.assertIn("Secure Financial Data Upload", valid.body.decode("utf-8"))
        self.assertEqual(valid.headers["referrer-policy"], "no-referrer")
        self.assertEqual(valid.headers["cache-control"], "no-store")
        self.assertEqual(invalid.status_code, 403)
        self.assertIn("invalid or has expired", invalid.body.decode("utf-8"))
        self.assertNotIn("cs_test", invalid.body.decode("utf-8"))

    def _paid_order_store(
        self,
        store: InMemoryOrderStore,
        email_provider: RecordingEmailProvider,
        event_id: str,
    ) -> InMemoryOrderStore:
        service = OrderFulfilmentService(store, settings(), email_provider)
        process_stripe_event(
            event(event_id, "checkout.session.completed", session(session_id=event_id)),
            store,
            settings(),
            fulfilment_service=service,
        )
        return store


class DummyRequest:
    def __init__(self, token: str):
        self.query_params = {"t": token}


class RecordingThenFailProvider(RecordingEmailProvider):
    def __init__(self, error: str) -> None:
        super().__init__()
        self.error = error

    def send(self, message, *, idempotency_key: str):
        self.sent.append((message, idempotency_key))
        from fulfilment.email_service import EmailDeliveryError

        raise EmailDeliveryError(self.error)


if __name__ == "__main__":
    unittest.main()
