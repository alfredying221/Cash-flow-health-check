from __future__ import annotations

import logging
import unittest
from dataclasses import replace
from datetime import timedelta

from fulfilment import app as app_module
from fulfilment.analysis_processor import process_order_analysis
from fulfilment.customer_sessions import create_customer_session, exchange_customer_token
from fulfilment.email_service import EmailDeliveryError, RecordingEmailProvider
from fulfilment.fulfilment_service import OrderFulfilmentService
from fulfilment.orders import InMemoryOrderStore, process_stripe_event
from fulfilment.result_delivery import (
    ResultDeliveryService,
    reissue_result_token,
    reproduce_result_token,
    validate_result_token,
)
from fulfilment.storage import InMemoryUploadStorage
from fulfilment.tokens import TokenValidationError, reproduce_token
from fulfilment.upload_intake import submit_upload
from tests.test_fulfilment_gate2 import event, session
from tests.test_fulfilment_gate4 import DummyRequest, csv_bytes, session_request, settings, valid_dataframe


class FailOnceEmailProvider(RecordingEmailProvider):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    def send(self, message, *, idempotency_key: str):
        if not self.failed_once:
            self.failed_once = True
            raise EmailDeliveryError("RESULT_EMAIL_TIMEOUT")
        return super().send(message, idempotency_key=idempotency_key)


class Gate6ResultDeliveryTests(unittest.TestCase):
    def ready_order(self, *, product_code: str = "FULL_ANALYSIS"):
        store, storage, order = create_validated_order(product_code=product_code)
        processed = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        return store, storage, processed.order

    def send_result_email(self, store, order, provider=None):
        provider = provider or RecordingEmailProvider()
        service = ResultDeliveryService(store, settings(), provider)
        sent = service.send_result_ready_email(order.order_id)
        token = provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0] if provider.sent else None
        return sent, token, provider

    def test_validated_upload_background_processing_reaches_ready_and_sends_result_once(self) -> None:
        store, storage, order = create_validated_order()
        provider = RecordingEmailProvider()
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        original_email_provider = app_module.get_email_provider
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        app_module.get_upload_storage = lambda settings: storage
        app_module.get_email_provider = lambda settings: provider
        try:
            app_module.process_validated_upload_background(order.order_id)
            app_module.process_validated_upload_background(order.order_id)
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
            app_module.get_upload_storage = original_storage
            app_module.get_email_provider = original_email_provider

        saved = store.get_order(order.order_id)
        self.assertEqual(saved.fulfilment_status, "READY")
        self.assertEqual(saved.analysis_status, "COMPLETED")
        self.assertEqual(saved.result_status, "READY")
        self.assertTrue(saved.pdf_object_path)
        self.assertTrue(saved.excel_object_path)
        self.assertTrue(saved.result_token_hash)
        self.assertEqual(saved.result_email_status, "SENT")
        self.assertEqual(len(provider.sent), 1)

    def test_full_analysis_result_page_access_and_safe_headers(self) -> None:
        store, storage, order = self.ready_order()
        _, result_token, _ = self.send_result_email(store, order)
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        try:
            response = app_module.result_page(session_request(store, order, "result"))
            invalid = app_module.result_page(DummyRequest())
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
        body = response.body.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertIn("SENALO Full Analysis", body)
        self.assertIn("Download PDF Report", body)
        self.assertIn("Download Excel Analysis", body)
        self.assertNotIn(order.order_id, body)
        self.assertNotIn(order.pdf_object_path, body)
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(invalid.headers["cache-control"], "no-store")

    def test_not_ready_invalid_expired_and_revoked_result_tokens_are_denied(self) -> None:
        store, storage, order = create_validated_order()
        provider = RecordingEmailProvider()
        service = ResultDeliveryService(store, settings(), provider)
        not_ready = service.send_result_ready_email(order.order_id)
        self.assertEqual(not_ready.result_email_status, "NOT_SENT")
        _, not_ready_token = reissue_result_token(
            order.order_id,
            store,
            derivation_secret=settings().token_derivation_secret,
            expiry_days=settings().result_token_expiry_days,
        )
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        try:
            not_ready_session = create_customer_session(order, "result", store)
            not_ready_page = app_module.result_page(DummyRequest(cookies={"senalo_customer_session": not_ready_session.raw_session_id}))
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
        self.assertEqual(not_ready_page.status_code, 403)

        store, storage, order = self.ready_order()
        sent, token, provider = self.send_result_email(store, order)
        self.assertIsNotNone(validate_result_token(token, store, derivation_secret=settings().token_derivation_secret))
        expired = store.save_order(replace(sent, result_token_expires_at=sent.result_token_created_at - timedelta(seconds=1)))
        with self.assertRaises(Exception):
            validate_result_token(token, store, derivation_secret=settings().token_derivation_secret)
        with self.assertRaises(Exception):
            exchange_customer_token(token, store, derivation_secret=settings().token_derivation_secret)
        updated, token = reissue_result_token(
            expired.order_id,
            store,
            derivation_secret=settings().token_derivation_secret,
            expiry_days=settings().result_token_expiry_days,
        )
        revoked = store.save_order(replace(updated, result_token_revoked_at=updated.result_token_created_at))
        with self.assertRaises(TokenValidationError):
            validate_result_token(token, store, derivation_secret=settings().token_derivation_secret)
        self.assertEqual(revoked.result_token_revoked_at, revoked.result_token_created_at)

    def test_upload_and_result_tokens_are_purpose_separated(self) -> None:
        store, storage, uploaded_order = create_validated_order()
        upload_token = reproduce_token(uploaded_order, settings().token_derivation_secret)
        processed = process_order_analysis(order_id=uploaded_order.order_id, store=store, storage=storage)
        _, result_token, _ = self.send_result_email(store, processed.order)
        with self.assertRaises(TokenValidationError):
            validate_result_token(upload_token, store, derivation_secret=settings().token_derivation_secret)

        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        try:
            result_session = create_customer_session(processed.order, "result", store)
            upload_response = app_module.upload_form(DummyRequest(cookies={"senalo_customer_session": result_session.raw_session_id}))
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
        self.assertEqual(upload_response.status_code, 403)

    def test_pdf_and_excel_downloads_are_authorized_with_fixed_headers_and_audit(self) -> None:
        store, storage, order = self.ready_order()
        _, result_token, _ = self.send_result_email(store, order)
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        app_module.get_upload_storage = lambda active_settings: storage
        try:
            request = session_request(store, order, "result")
            pdf = app_module.download_pdf(request)
            excel = app_module.download_excel(request)
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
            app_module.get_upload_storage = original_storage
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.media_type, "application/pdf")
        self.assertEqual(pdf.headers["content-disposition"], 'attachment; filename="SENALO-Full-Analysis.pdf"')
        self.assertTrue(pdf.body.startswith(b"%PDF-1.4"))
        self.assertEqual(excel.status_code, 200)
        self.assertEqual(
            excel.media_type,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(excel.headers["content-disposition"], 'attachment; filename="SENALO-Full-Analysis.xlsx"')
        self.assertEqual(store.get_order(order.order_id).download_count, 2)
        self.assertIsNotNone(store.get_order(order.order_id).last_download_at)

    def test_download_does_not_accept_arbitrary_paths_and_handles_missing_result_object(self) -> None:
        store, storage, order = self.ready_order()
        _, result_token, _ = self.send_result_email(store, order)
        storage.objects.pop(order.pdf_object_path)
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        app_module.get_upload_storage = lambda active_settings: storage
        try:
            request = session_request(store, order, "result")
            response = app_module.download_pdf(request)
            arbitrary = app_module.download_artifact(request, "path=uploads/something")
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
            app_module.get_upload_storage = original_storage
        self.assertEqual(response.status_code, 403)
        self.assertEqual(arbitrary.status_code, 403)
        self.assertNotIn("uploads/", response.body.decode("utf-8"))

    def test_result_email_idempotency_retry_reissue_and_no_regeneration(self) -> None:
        store, storage, order = self.ready_order()
        provider = RecordingEmailProvider()
        sent = ResultDeliveryService(store, settings(), provider).send_result_ready_email(order.order_id)
        second = ResultDeliveryService(store, settings(), provider).send_result_ready_email(order.order_id)
        self.assertEqual(sent.result_email_status, "SENT")
        self.assertEqual(second.result_email_status, "SENT")
        self.assertEqual(len(provider.sent), 1)
        self.assertEqual(provider.sent[0][1], f"result-ready/{order.order_id}")
        original_token = provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0]
        self.assertEqual(reproduce_result_token(sent, settings().token_derivation_secret), original_token)

        provider = FailOnceEmailProvider()
        store, storage, order = self.ready_order()
        pdf_path = order.pdf_object_path
        excel_path = order.excel_object_path
        failed = ResultDeliveryService(store, settings(), provider).send_result_ready_email(order.order_id)
        failed_token = reproduce_result_token(failed, settings().token_derivation_secret)
        retry = ResultDeliveryService(store, settings(), provider).send_result_ready_email(order.order_id)
        retry_token = provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0]
        self.assertEqual(failed.result_email_status, "FAILED")
        self.assertEqual(retry.result_email_status, "SENT")
        self.assertEqual(failed_token, retry_token)
        self.assertEqual(provider.sent[0][1], f"result-ready/{order.order_id}")
        self.assertEqual(store.get_order(order.order_id).pdf_object_path, pdf_path)
        self.assertEqual(store.get_order(order.order_id).excel_object_path, excel_path)

        reissued, new_token = reissue_result_token(
            order.order_id,
            store,
            derivation_secret=settings().token_derivation_secret,
            expiry_days=settings().result_token_expiry_days,
        )
        self.assertNotEqual(new_token, retry_token)
        with self.assertRaises(TokenValidationError):
            validate_result_token(retry_token, store, derivation_secret=settings().token_derivation_secret)
        self.assertIsNotNone(validate_result_token(new_token, store, derivation_secret=settings().token_derivation_secret))

    def test_result_page_marks_delivered_and_expert_review_is_blocked(self) -> None:
        store, storage, order = self.ready_order()
        _, result_token, _ = self.send_result_email(store, order)
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        try:
            app_module.result_page(session_request(store, order, "result"))
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
        self.assertIsNotNone(store.get_order(order.order_id).delivered_at)

        expert_store, expert_storage, expert_order = self.ready_order(product_code="EXPERT_REVIEW")
        provider = RecordingEmailProvider()
        expert_after = ResultDeliveryService(expert_store, settings(), provider).send_result_ready_email(expert_order.order_id)
        self.assertEqual(expert_after.result_email_status, "NOT_SENT")
        self.assertEqual(len(provider.sent), 0)
        reissued, expert_token = reissue_result_token(
            expert_order.order_id,
            expert_store,
            derivation_secret=settings().token_derivation_secret,
            expiry_days=settings().result_token_expiry_days,
        )
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        app_module.FirestoreOrderStore = lambda project=None: expert_store
        app_module.Settings.from_env = settings
        app_module.get_upload_storage = lambda active_settings: expert_storage
        try:
            request = session_request(expert_store, expert_order, "result")
            page = app_module.result_page(request)
            pdf = app_module.download_pdf(request)
            excel = app_module.download_excel(request)
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
            app_module.get_upload_storage = original_storage
        self.assertEqual(page.status_code, 200)
        self.assertIn("Expert Review is being prepared", page.body.decode("utf-8"))
        self.assertNotIn("Download PDF", page.body.decode("utf-8"))
        self.assertEqual(pdf.status_code, 403)
        self.assertEqual(excel.status_code, 403)

    def test_delivery_logs_do_not_include_sensitive_data_and_storage_stays_private(self) -> None:
        store, storage, order = self.ready_order()
        provider = RecordingEmailProvider()
        with self.assertLogs("senalo.fulfilment", level=logging.INFO) as captured:
            sent = ResultDeliveryService(store, settings(), provider).send_result_ready_email(order.order_id)
        token = provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0]
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        app_module.get_upload_storage = lambda active_settings: storage
        try:
            with self.assertLogs("senalo.fulfilment", level=logging.INFO) as download_logs:
                app_module.download_pdf(session_request(store, order, "result"))
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
            app_module.get_upload_storage = original_storage
        logs = "\n".join(captured.output + download_logs.output)
        for forbidden in [
            token,
            "/result?t=",
            "signed",
            "10000",
            "Jane Owner",
            "jane@example.com",
            "100000",
            "uploads/",
            sent.pdf_object_path,
            sent.excel_object_path,
        ]:
            self.assertNotIn(str(forbidden), logs)
        self.assertTrue(sent.pdf_object_path.startswith(f"results/{order.order_id}/"))
        self.assertTrue(sent.excel_object_path.startswith(f"results/{order.order_id}/"))
        self.assertNotIn("Jane Owner", sent.pdf_object_path)
        self.assertNotIn("jane@example.com", sent.excel_object_path)


def create_validated_order(*, product_code: str = "FULL_ANALYSIS"):
    store = InMemoryOrderStore()
    storage = InMemoryUploadStorage()
    provider = RecordingEmailProvider()
    service = OrderFulfilmentService(store, settings(), provider)
    if product_code == "EXPERT_REVIEW":
        checkout_session = session(
            session_id="cs_test_expert_gate6",
            price_id="price_expert_test",
            product_id="prod_expert_test",
            amount_total=14900,
            metadata_code="EXPERT_REVIEW",
        )
    else:
        checkout_session = session(session_id="cs_test_full_gate6")
    process_stripe_event(
        event(f"evt_{product_code}_gate6", "checkout.session.completed", checkout_session),
        store,
        settings(),
        fulfilment_service=service,
    )
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
    return store, storage, uploaded.order


if __name__ == "__main__":
    unittest.main()
