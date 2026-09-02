from __future__ import annotations

import logging
import asyncio
import unittest
from http.cookies import SimpleCookie
from dataclasses import replace
from datetime import timedelta
from logging.handlers import BufferingHandler
from urllib.parse import urldefrag

from fulfilment import app as app_module
from fulfilment.analysis_processor import process_order_analysis
from fulfilment.config import Settings
from fulfilment.customer_sessions import (
    CUSTOMER_SESSION_COOKIE,
    CustomerSessionError,
    create_customer_session,
    exchange_customer_token,
    validate_customer_session,
)
from fulfilment.email_service import RecordingEmailProvider
from fulfilment.fulfilment_service import OrderFulfilmentService
from fulfilment.models import utc_now
from fulfilment.operator_review import OperatorAuthError, authenticate_operator, save_review_draft
from fulfilment.orders import InMemoryOrderStore, process_stripe_event
from fulfilment.result_delivery import ResultDeliveryService, reissue_result_token
from fulfilment.storage import InMemoryUploadStorage
from fulfilment.tokens import reissue_token
from fulfilment.upload_intake import submit_upload
from tests.test_fulfilment_gate2 import event, session
from tests.test_fulfilment_gate4 import JsonRequest, csv_bytes, settings as base_settings, valid_dataframe
from tests.test_fulfilment_gate7 import DummyRequest, OperatorIdentity, release_expert_review


def settings(**overrides) -> Settings:
    source = base_settings()
    values = {
        "stripe_webhook_secret": source.stripe_webhook_secret,
        "stripe_secret_key": source.stripe_secret_key,
        "google_cloud_project": source.google_cloud_project,
        "full_analysis_price_id": source.full_analysis_price_id,
        "expert_review_price_id": source.expert_review_price_id,
        "full_analysis_product_id": source.full_analysis_product_id,
        "expert_review_product_id": source.expert_review_product_id,
        "resend_api_key": source.resend_api_key,
        "senalo_email_from": source.senalo_email_from,
        "senalo_email_reply_to": source.senalo_email_reply_to,
        "senalo_public_fulfilment_base_url": source.senalo_public_fulfilment_base_url,
        "token_expiry_days": source.token_expiry_days,
        "result_token_expiry_days": source.result_token_expiry_days,
        "token_derivation_secret": source.token_derivation_secret,
        "upload_bucket": source.upload_bucket,
        "max_upload_bytes": source.max_upload_bytes,
        "operator_auth_token": source.operator_auth_token,
        "deployment_role": source.deployment_role,
        "operator_audit_id": source.operator_audit_id,
        "customer_session_minutes": source.customer_session_minutes,
    }
    values.update(overrides)
    return Settings(**values)


def paid_upload_order() -> tuple[InMemoryOrderStore, InMemoryUploadStorage, str]:
    store = InMemoryOrderStore()
    storage = InMemoryUploadStorage()
    provider = RecordingEmailProvider()
    service = OrderFulfilmentService(store, settings(), provider)
    process_stripe_event(
        event("evt_d2_upload", "checkout.session.completed", session(session_id="cs_d2_upload")),
        store,
        settings(),
        fulfilment_service=service,
    )
    token = provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0]
    return store, storage, token


def ready_result_order(product_code: str = "FULL_ANALYSIS"):
    store = InMemoryOrderStore()
    storage = InMemoryUploadStorage()
    provider = RecordingEmailProvider()
    service = OrderFulfilmentService(store, settings(), provider)
    checkout = session(session_id=f"cs_d2_{product_code.lower()}")
    if product_code == "EXPERT_REVIEW":
        checkout = session(
            session_id="cs_d2_expert",
            price_id="price_expert_test",
            product_id="prod_expert_test",
            amount_total=14900,
            metadata_code="EXPERT_REVIEW",
        )
    process_stripe_event(event(f"evt_d2_{product_code}", "checkout.session.completed", checkout), store, settings(), fulfilment_service=service)
    order = next(iter(store.orders.values()))
    uploaded = submit_upload(
        order=order,
        store=store,
        storage=storage,
        business_type="Food & Beverage",
        opening_cash_value="100000",
        filename="financials.csv",
        content=csv_bytes(valid_dataframe()),
        max_upload_bytes=settings().max_upload_bytes,
    )
    processed = process_order_analysis(order_id=uploaded.order.order_id, store=store, storage=storage)
    return store, storage, processed.order


def session_cookie_from(response) -> str:
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie[CUSTOMER_SESSION_COOKIE].value


class GateD2SecurityHardeningTests(unittest.TestCase):
    def patch_app(self, store, storage=None, active_settings=None):
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = lambda: active_settings or settings()
        if storage is not None:
            app_module.get_upload_storage = lambda settings: storage
        return original_store, original_settings, original_storage

    def restore_app(self, originals):
        app_module.FirestoreOrderStore, app_module.Settings.from_env, app_module.get_upload_storage = originals

    def test_access_page_uses_fragment_bootstrap_only_and_no_browser_storage(self) -> None:
        response = app_module.access_bootstrap()
        body = response.body.decode("utf-8")
        self.assertIn("/session/exchange", body)
        self.assertIn("window.location.hash", body)
        self.assertIn('replaceState(null, "", "/access")', body)
        self.assertNotIn("?t=", body)
        self.assertNotIn("localStorage", body)
        self.assertNotIn("sessionStorage", body)
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_fragment_token_is_not_sent_in_get_access_request(self) -> None:
        clean_url, fragment = urldefrag("https://testserver/access#TEST_TOKEN_FRAGMENT_123")
        response = app_module.access_bootstrap()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(clean_url, "https://testserver/access")
        self.assertEqual(fragment, "TEST_TOKEN_FRAGMENT_123")

    def test_session_exchange_valid_invalid_expired_and_revoked_tokens(self) -> None:
        store, _, token = paid_upload_order()
        order = next(iter(store.orders.values()))
        result = exchange_customer_token(token, store, derivation_secret=settings().token_derivation_secret)
        self.assertEqual(result.next_path, "/upload")
        self.assertNotEqual(result.session.session_hash, result.raw_session_id)
        self.assertNotIn(result.raw_session_id, str(store.customer_sessions))
        self.assertIn(result.session.session_hash, store.customer_sessions)
        self.assertEqual(validate_customer_session(result.raw_session_id, "upload", store)[1].order_id, order.order_id)
        with self.assertRaises(CustomerSessionError):
            exchange_customer_token("wrong_token_value_that_is_long_enough", store, derivation_secret=settings().token_derivation_secret)

        expired = store.save_order(replace(order, token_expires_at=utc_now() - timedelta(seconds=1)))
        with self.assertRaises(CustomerSessionError):
            exchange_customer_token(token, store, derivation_secret=settings().token_derivation_secret)
        active, revoked_token = reissue_token(expired.order_id, store, derivation_secret=settings().token_derivation_secret)
        store.save_order(replace(active, token_revoked_at=active.token_created_at))
        with self.assertRaises(CustomerSessionError):
            exchange_customer_token(revoked_token, store, derivation_secret=settings().token_derivation_secret)

    def test_exchange_endpoint_sets_secure_httponly_cookie_and_logs_no_credentials(self) -> None:
        store, _, token = paid_upload_order()
        originals = self.patch_app(store)
        capture = BufferingHandler(capacity=100)
        logger = logging.getLogger("senalo.fulfilment")
        logger.addHandler(capture)
        try:
            invalid = asyncio.run(app_module.session_exchange(JsonRequest({"token": "wrong_token_value_that_is_long_enough"})))
            valid = asyncio.run(app_module.session_exchange(JsonRequest({"token": token})))
        finally:
            logger.removeHandler(capture)
            self.restore_app(originals)
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(valid.status_code, 200)
        cookie = valid.headers["set-cookie"]
        self.assertIn(CUSTOMER_SESSION_COOKIE, cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertIn("Secure", cookie)
        self.assertIn("SameSite=lax", cookie)
        logs = "\n".join(record.getMessage() for record in capture.buffer)
        self.assertNotIn(token, logs)

    def test_upload_result_and_download_routes_require_sessions_and_keep_purpose_separation(self) -> None:
        store, storage, token = paid_upload_order()
        originals = self.patch_app(store, storage)
        try:
            self.assertEqual(app_module.upload_form(DummyRequest(cookies={})).status_code, 403)
            exchange = asyncio.run(app_module.session_exchange(JsonRequest({"token": token})))
            self.assertEqual(exchange.status_code, 200)
            cookie = session_cookie_from(exchange)
            self.assertEqual(app_module.upload_form(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: cookie})).status_code, 200)
            self.assertEqual(app_module.result_page(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: cookie})).status_code, 403)
        finally:
            self.restore_app(originals)

        store, storage, order = ready_result_order()
        provider = RecordingEmailProvider()
        sent = ResultDeliveryService(store, settings(), provider).send_result_ready_email(order.order_id)
        result_token = provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0]
        originals = self.patch_app(store, storage)
        try:
            self.assertEqual(app_module.result_page(DummyRequest(cookies={})).status_code, 403)
            exchange = asyncio.run(app_module.session_exchange(JsonRequest({"token": result_token})))
            self.assertEqual(exchange.status_code, 200)
            cookie = session_cookie_from(exchange)
            request = DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: cookie})
            page = app_module.result_page(request)
            pdf = app_module.download_pdf(request)
            excel = app_module.download_excel(request)
        finally:
            self.restore_app(originals)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(store.get_order(sent.order_id).download_count, 2)

    def test_expired_and_invalid_sessions_are_rejected(self) -> None:
        store, _, token = paid_upload_order()
        order = next(iter(store.orders.values()))
        session = create_customer_session(order, "upload", store, now=utc_now() - timedelta(hours=2), session_minutes=45)
        with self.assertRaises(CustomerSessionError):
            validate_customer_session(session.raw_session_id, "upload", store)
        with self.assertRaises(CustomerSessionError):
            validate_customer_session("wrong_session_value_that_is_long_enough", "upload", store)
        fresh = exchange_customer_token(token, store, derivation_secret=settings().token_derivation_secret)
        with self.assertRaises(CustomerSessionError):
            validate_customer_session(fresh.raw_session_id, "result", store)

    def test_expert_review_result_session_blocks_until_released_then_serves_final_artifacts(self) -> None:
        store, storage, order = ready_result_order("EXPERT_REVIEW")
        _, token = reissue_result_token(order.order_id, store, derivation_secret=settings().token_derivation_secret)
        session_result = exchange_customer_token(token, store, derivation_secret=settings().token_derivation_secret)
        originals = self.patch_app(store, storage)
        try:
            blocked = app_module.result_page(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: session_result.raw_session_id}))
            pending_pdf = app_module.download_pdf(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: session_result.raw_session_id}))
        finally:
            self.restore_app(originals)
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("being prepared", blocked.body.decode("utf-8"))
        self.assertEqual(pending_pdf.status_code, 403)

        released = release_expert_review(
            order_id=order.order_id,
            store=store,
            storage=storage,
            settings=settings(),
            email_provider=RecordingEmailProvider(),
            operator=OperatorIdentity("qa"),
            commentary="Reviewed and ready.",
            action_values=["Action one", "Action two", "Action three"],
            confirmation="approve-release",
        )
        fresh_session = create_customer_session(released, "result", store)
        originals = self.patch_app(store, storage)
        try:
            pdf = app_module.download_pdf(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: fresh_session.raw_session_id}))
            excel = app_module.download_excel(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: fresh_session.raw_session_id}))
        finally:
            self.restore_app(originals)
        self.assertEqual(pdf.headers["content-disposition"], 'attachment; filename="SENALO-Expert-Review.pdf"')
        self.assertEqual(excel.headers["content-disposition"], 'attachment; filename="SENALO-Expert-Review.xlsx"')

    def test_generated_customer_urls_do_not_contain_credentials_after_exchange(self) -> None:
        store, _, token = paid_upload_order()
        order = next(iter(store.orders.values()))
        exchange = exchange_customer_token(token, store, derivation_secret=settings().token_derivation_secret)
        self.assertEqual(exchange.next_path, "/upload")
        page = app_module.render_upload_form()
        self.assertNotIn(token, page)
        self.assertNotIn(exchange.raw_session_id, page)
        self.assertNotIn("?t=", page)
        self.assertNotIn(f"/orders/{order.order_id}", page)

    def test_customer_routes_retain_security_headers(self) -> None:
        for response in [app_module.generic_denial_response(), app_module.generic_result_denial_response()]:
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.headers["referrer-policy"], "no-referrer")
            self.assertEqual(response.headers["x-content-type-options"], "nosniff")
            self.assertEqual(response.headers["x-frame-options"], "DENY")
            self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_operator_local_production_and_public_modes(self) -> None:
        local = settings(operator_auth_token="operator-secret")
        with self.assertRaises(OperatorAuthError):
            authenticate_operator(DummyRequest(headers={}), local)
        operator = authenticate_operator(
            DummyRequest(headers={"x-senalo-operator-token": "operator-secret", "x-senalo-operator-id": "qa"}),
            local,
        )
        self.assertEqual(operator.operator_id, "qa")

        production = settings(deployment_role="operator", operator_auth_token=None, operator_audit_id="alfredying221@gmail.com")
        self.assertEqual(authenticate_operator(DummyRequest(headers={}), production).operator_id, "alfredying221@gmail.com")
        public = settings(deployment_role="public", operator_auth_token="operator-secret", operator_audit_id="alfredying221@gmail.com")
        with self.assertRaises(OperatorAuthError):
            authenticate_operator(DummyRequest(headers={"x-senalo-operator-token": "operator-secret"}), public)

    def test_public_route_mode_disables_operator_pages_and_operator_mode_records_audit_id(self) -> None:
        store, storage, order = ready_result_order("EXPERT_REVIEW")
        originals = self.patch_app(store, storage, settings(deployment_role="public", operator_auth_token="operator-secret"))
        try:
            denied = app_module.operator_reviews(DummyRequest(headers={"x-senalo-operator-token": "operator-secret"}))
        finally:
            self.restore_app(originals)
        self.assertEqual(denied.status_code, 403)

        originals = self.patch_app(
            store,
            storage,
            settings(deployment_role="operator", operator_auth_token=None, operator_audit_id="alfredying221@gmail.com"),
        )
        try:
            listing = app_module.operator_reviews(DummyRequest(headers={}))
            saved = save_review_draft(
                order_id=order.order_id,
                store=store,
                operator=OperatorIdentity("alfredying221@gmail.com"),
                commentary="Reviewed for D2.",
                action_values=["Action one", "Action two", "Action three"],
            )
        finally:
            self.restore_app(originals)
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(saved.review_operator_id, "alfredying221@gmail.com")


if __name__ == "__main__":
    unittest.main()
