from __future__ import annotations

import asyncio
import io
import logging
import unittest
from dataclasses import replace

import pandas as pd
from fastapi import BackgroundTasks
from openpyxl import Workbook
from starlette.datastructures import UploadFile

from fulfilment import app as app_module
from fulfilment.config import Settings
from fulfilment.customer_sessions import CUSTOMER_SESSION_COOKIE, create_customer_session, exchange_customer_token
from fulfilment.email_service import RecordingEmailProvider
from fulfilment.fulfilment_service import OrderFulfilmentService
from fulfilment.models import Order
from fulfilment.orders import InMemoryOrderStore, process_stripe_event
from fulfilment.storage import InMemoryUploadStorage, UploadStorageError
from fulfilment.tokens import reissue_token
from fulfilment.upload_intake import (
    InvalidBusinessTypeError,
    InvalidOpeningCashError,
    InvalidUploadError,
    UploadNotAllowedError,
    ValidationFailedError,
    submit_upload,
)
from tests.test_fulfilment_gate2 import event, session


SECRET = "test_derivation_secret_32_bytes_minimum"


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
        token_derivation_secret=SECRET,
        max_upload_bytes=5 * 1024 * 1024,
    )


def valid_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Month": ["2026-01", "2026-02", "2026-03"],
            "Sales": [10000, 11000, 12000],
            "Direct Costs": [3500, 3800, 4100],
            "Labour Cost": [2500, 2600, 2700],
            "Occupancy Cost": [1200, 1200, 1200],
            "Other Operating Costs": [900, 950, 1000],
        }
    )


def legacy_dataframe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Month": ["2026-01", "2026-02", "2026-03"],
            "Revenue": [10000, 11000, 12000],
            "COGS": [3500, 3800, 4100],
            "Payroll": [2500, 2600, 2700],
            "Rent": [1200, 1200, 1200],
            "Marketing": [300, 350, 400],
            "Other Expenses": [600, 600, 600],
        }
    )


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8")


def xlsx_bytes(df: pd.DataFrame) -> bytes:
    output = io.BytesIO()
    df.to_excel(output, index=False)
    return output.getvalue()


def formula_xlsx_bytes() -> bytes:
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Month", "Sales", "Direct Costs", "Labour Cost", "Occupancy Cost", "Other Operating Costs"])
    worksheet.append(["2026-01", "=10000", "=3500", "=2500", "=1200", "=900"])
    worksheet.append(["2026-02", "=11000", "=3800", "=2600", "=1200", "=950"])
    worksheet.append(["2026-03", "=12000", "=4100", "=2700", "=1200", "=1000"])
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def paid_order_and_token(status: str = "AWAITING_UPLOAD") -> tuple[InMemoryOrderStore, Order, str]:
    store = InMemoryOrderStore()
    email_provider = RecordingEmailProvider()
    service = OrderFulfilmentService(store, settings(), email_provider)
    process_stripe_event(
        event("evt_upload_order", "checkout.session.completed", session()),
        store,
        settings(),
        fulfilment_service=service,
    )
    order = next(iter(store.orders.values()))
    if status != order.fulfilment_status:
        order = store.save_order(replace(order, fulfilment_status=status))
    token = email_provider.sent[0][0].body.split("/access#", 1)[1].splitlines()[0]
    return store, order, token


def session_request(store: InMemoryOrderStore, order: Order, purpose: str = "upload") -> "DummyRequest":
    session = create_customer_session(order, purpose, store)
    return DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: session.raw_session_id})


class FailingSaveStore(InMemoryOrderStore):
    def __init__(self, source: InMemoryOrderStore) -> None:
        super().__init__()
        self.events = source.events
        self.orders = source.orders
        self.checkout_index = source.checkout_index

    def save_order(self, order: Order) -> Order:
        raise RuntimeError("firestore update failed")


class Gate4UploadIntakeTests(unittest.TestCase):
    def test_valid_token_displays_intake_form_and_invalid_tokens_are_rejected(self) -> None:
        store, _, token = paid_order_and_token()
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        try:
            exchange = exchange_customer_token(token, store, derivation_secret=SECRET)
            valid = app_module.upload_form(DummyRequest(cookies={CUSTOMER_SESSION_COOKIE: exchange.raw_session_id}))
            invalid = app_module.upload_form(DummyRequest())
            expired_order, expired_token = reissue_token(
                next(iter(store.orders)), store, derivation_secret=SECRET
            )
            store.save_order(replace(expired_order, token_expires_at=expired_order.token_created_at))
            expired_exchange = asyncio.run(app_module.session_exchange(JsonRequest({"token": expired_token})))
            revoked_order, revoked_token = reissue_token(
                next(iter(store.orders)), store, derivation_secret=SECRET
            )
            store.save_order(replace(revoked_order, token_revoked_at=revoked_order.token_created_at))
            revoked_exchange = asyncio.run(app_module.session_exchange(JsonRequest({"token": revoked_token})))
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings

        self.assertEqual(valid.status_code, 200)
        self.assertIn("Secure Financial Data Upload", valid.body.decode())
        self.assertIn("Business Type", valid.body.decode())
        self.assertEqual(invalid.status_code, 403)
        self.assertEqual(expired_exchange.status_code, 403)
        self.assertEqual(revoked_exchange.status_code, 403)

    def test_valid_csv_and_xlsx_current_schema_validate(self) -> None:
        for filename, content in [
            ("financials.csv", csv_bytes(valid_dataframe())),
            ("financials.xlsx", xlsx_bytes(valid_dataframe())),
        ]:
            with self.subTest(filename=filename):
                store, order, _ = paid_order_and_token()
                storage = InMemoryUploadStorage()
                result = submit_upload(
                    order=order,
                    store=store,
                    storage=storage,
                    business_type="Food & Beverage",
                    opening_cash_value="0",
                    filename=filename,
                    content=content,
                    max_upload_bytes=settings().max_upload_bytes,
                )
                self.assertEqual(result.order.fulfilment_status, "VALIDATED")
                self.assertEqual(result.order.validation_status, "VALIDATED")
                self.assertEqual(result.order.opening_cash, 0)
                self.assertIn(result.order.upload_object_path, storage.objects)

    def test_legacy_csv_and_xlsx_normalize_and_validate(self) -> None:
        for filename, content in [
            ("legacy.csv", csv_bytes(legacy_dataframe())),
            ("legacy.xlsx", xlsx_bytes(legacy_dataframe())),
        ]:
            with self.subTest(filename=filename):
                store, order, _ = paid_order_and_token()
                result = submit_upload(
                    order=order,
                    store=store,
                    storage=InMemoryUploadStorage(),
                    business_type="Independent Retail",
                    opening_cash_value="1000",
                    filename=filename,
                    content=content,
                    max_upload_bytes=settings().max_upload_bytes,
                )
                self.assertEqual(result.order.fulfilment_status, "VALIDATED")

    def test_validation_failures_mark_order_recoverable(self) -> None:
        cases = [
            ("missing.csv", valid_dataframe().drop(columns=["Labour Cost"]), "Missing required columns"),
            ("invalid_numeric.csv", valid_dataframe().assign(Sales=["bad", 100, 200]), "Sales contains a non-numeric value"),
            ("duplicate_month.csv", valid_dataframe().assign(Month=["2026-01", "2026-01", "2026-03"]), "Month appears more than once"),
        ]
        for filename, dataframe, expected in cases:
            with self.subTest(filename=filename):
                store, order, _ = paid_order_and_token()
                with self.assertRaises(ValidationFailedError) as raised:
                    submit_upload(
                        order=order,
                        store=store,
                        storage=InMemoryUploadStorage(),
                        business_type="Food & Beverage",
                        opening_cash_value="100",
                        filename=filename,
                        content=csv_bytes(dataframe),
                        max_upload_bytes=settings().max_upload_bytes,
                    )
                self.assertIn(expected, str(raised.exception))
                saved = store.get_order(order.order_id)
                self.assertEqual(saved.fulfilment_status, "VALIDATION_FAILED")
                self.assertEqual(saved.validation_status, "FAILED")

    def test_business_types_and_opening_cash_validation(self) -> None:
        for business_type in [
            "Food & Beverage",
            "Market Stall / Vendor",
            "Independent Retail",
            "Other Owner-Operated Business",
        ]:
            store, order, _ = paid_order_and_token()
            result = submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(),
                business_type=business_type,
                opening_cash_value="0",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
            self.assertEqual(result.order.business_type, business_type)

        store, order, _ = paid_order_and_token()
        with self.assertRaises(InvalidBusinessTypeError):
            submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(),
                business_type="Other",
                opening_cash_value="0",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
        with self.assertRaises(InvalidOpeningCashError):
            submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(),
                business_type="Food & Beverage",
                opening_cash_value="NaN",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
        with self.assertRaises(InvalidOpeningCashError):
            submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(),
                business_type="Food & Beverage",
                opening_cash_value="Infinity",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )

    def test_unsupported_unsafe_and_oversized_files_rejected(self) -> None:
        cases = [
            ("script.exe", b"MZnotreally", InvalidUploadError),
            ("macro.xlsm", xlsx_bytes(valid_dataframe()), InvalidUploadError),
            ("old.xls", b"old excel", InvalidUploadError),
            ("bad.xlsx", b"not a zip", InvalidUploadError),
            ("binary.csv", b"MZ,Sales\x00bad", InvalidUploadError),
            ("binary.xlsx", b"MZnotreally", InvalidUploadError),
            ("empty.csv", b"", InvalidUploadError),
        ]
        for filename, content, error_type in cases:
            store, order, _ = paid_order_and_token()
            with self.subTest(filename=filename), self.assertRaises(error_type):
                submit_upload(
                    order=order,
                    store=store,
                    storage=InMemoryUploadStorage(),
                    business_type="Food & Beverage",
                    opening_cash_value="100",
                    filename=filename,
                    content=content,
                    max_upload_bytes=settings().max_upload_bytes,
                )

        store, order, _ = paid_order_and_token()
        with self.assertRaises(ValidationFailedError):
            submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(),
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="too_large.csv",
                content=b"a" * 11,
                max_upload_bytes=10,
            )

    def test_formula_bearing_required_xlsx_cells_are_rejected_before_storage(self) -> None:
        store, order, _ = paid_order_and_token()
        storage = InMemoryUploadStorage()
        with self.assertRaises(ValidationFailedError) as raised:
            submit_upload(
                order=order,
                store=store,
                storage=storage,
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="formula.xlsx",
                content=formula_xlsx_bytes(),
                max_upload_bytes=settings().max_upload_bytes,
            )
        self.assertIn("Please replace formulas", str(raised.exception))
        saved = store.get_order(order.order_id)
        self.assertEqual(saved.fulfilment_status, "VALIDATION_FAILED")
        self.assertEqual(saved.validation_status, "FAILED")
        self.assertIsNone(saved.upload_object_path)
        self.assertEqual(storage.objects, {})

    def test_successful_upload_stores_no_financial_rows_in_order(self) -> None:
        store, order, _ = paid_order_and_token()
        result = submit_upload(
            order=order,
            store=store,
            storage=InMemoryUploadStorage(),
            business_type="Market Stall / Vendor",
            opening_cash_value="100",
            filename="private client q1.csv",
            content=csv_bytes(valid_dataframe()),
            max_upload_bytes=settings().max_upload_bytes,
        )
        order_text = str(result.order.to_dict())
        self.assertNotIn("10000", order_text)
        self.assertNotIn("Sales", order_text)
        self.assertIn("private client q1.csv", order_text)

    def test_reupload_replaces_previous_object(self) -> None:
        store, order, _ = paid_order_and_token()
        storage = InMemoryUploadStorage()
        first = submit_upload(
            order=order,
            store=store,
            storage=storage,
            business_type="Food & Beverage",
            opening_cash_value="100",
            filename="first.csv",
            content=csv_bytes(valid_dataframe()),
            max_upload_bytes=settings().max_upload_bytes,
        )
        second = submit_upload(
            order=first.order,
            store=store,
            storage=storage,
            business_type="Food & Beverage",
            opening_cash_value="100",
            filename="second.csv",
            content=csv_bytes(valid_dataframe()),
            max_upload_bytes=settings().max_upload_bytes,
        )
        self.assertNotEqual(first.order.upload_object_path, second.order.upload_object_path)
        self.assertIn(first.order.upload_object_path, storage.deleted)
        self.assertNotIn(first.order.upload_object_path, storage.objects)

    def test_failed_reupload_leaves_previous_canonical_upload_intact(self) -> None:
        store, order, _ = paid_order_and_token()
        storage = InMemoryUploadStorage()
        first = submit_upload(
            order=order,
            store=store,
            storage=storage,
            business_type="Food & Beverage",
            opening_cash_value="100",
            filename="first.csv",
            content=csv_bytes(valid_dataframe()),
            max_upload_bytes=settings().max_upload_bytes,
        )
        original_path = first.order.upload_object_path
        original_bytes = storage.objects[original_path]

        with self.assertRaises(ValidationFailedError):
            submit_upload(
                order=first.order,
                store=store,
                storage=storage,
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="replacement.csv",
                content=csv_bytes(valid_dataframe().drop(columns=["Sales"])),
                max_upload_bytes=settings().max_upload_bytes,
            )
        saved = store.get_order(order.order_id)
        self.assertEqual(saved.upload_object_path, original_path)
        self.assertEqual(storage.objects[original_path], original_bytes)

        storage.fail_save = True
        with self.assertRaises(UploadStorageError):
            submit_upload(
                order=saved,
                store=store,
                storage=storage,
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="replacement.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
        saved = store.get_order(order.order_id)
        self.assertEqual(saved.upload_object_path, original_path)
        self.assertEqual(storage.objects[original_path], original_bytes)

        storage.fail_save = False
        failing_store = FailingSaveStore(store)
        with self.assertRaises(RuntimeError):
            submit_upload(
                order=saved,
                store=failing_store,
                storage=storage,
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="replacement.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
        saved = store.get_order(order.order_id)
        self.assertEqual(saved.upload_object_path, original_path)
        self.assertEqual(storage.objects[original_path], original_bytes)
        self.assertEqual(len(storage.objects), 1)

    def test_storage_failure_and_store_failure_are_recoverable(self) -> None:
        store, order, _ = paid_order_and_token()
        with self.assertRaises(UploadStorageError):
            submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(fail_save=True),
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
        self.assertEqual(store.get_order(order.order_id).fulfilment_status, "AWAITING_UPLOAD")

        source_store, order, _ = paid_order_and_token()
        failing_store = FailingSaveStore(source_store)
        storage = InMemoryUploadStorage()
        with self.assertRaises(RuntimeError):
            submit_upload(
                order=order,
                store=failing_store,
                storage=storage,
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )
        self.assertEqual(len(storage.objects), 0)
        self.assertEqual(len(storage.deleted), 1)

    def test_upload_not_allowed_before_paid_awaiting_upload(self) -> None:
        store, order, _ = paid_order_and_token(status="NOT_STARTED")
        with self.assertRaises(UploadNotAllowedError):
            submit_upload(
                order=order,
                store=store,
                storage=InMemoryUploadStorage(),
                business_type="Food & Beverage",
                opening_cash_value="100",
                filename="financials.csv",
                content=csv_bytes(valid_dataframe()),
                max_upload_bytes=settings().max_upload_bytes,
            )

    def test_upload_route_success_logs_no_token_or_financial_values_and_headers_present(self) -> None:
        store, order, token = paid_order_and_token()
        storage = InMemoryUploadStorage()
        original_store = app_module.FirestoreOrderStore
        original_settings = app_module.Settings.from_env
        original_storage = app_module.get_upload_storage
        app_module.FirestoreOrderStore = lambda project=None: store
        app_module.Settings.from_env = settings
        app_module.get_upload_storage = lambda settings: storage
        upload_file = UploadFile(file=io.BytesIO(csv_bytes(valid_dataframe())), filename="client-private.csv")
        background_tasks = BackgroundTasks()
        try:
            with self.assertLogs("senalo.fulfilment", level=logging.INFO) as captured:
                response = asyncio.run(
                    app_module.upload_submit(
                        session_request(store, order),
                        background_tasks,
                        t=None,
                        business_type="Food & Beverage",
                        opening_cash="100",
                        financial_file=upload_file,
                    )
                )
        finally:
            app_module.FirestoreOrderStore = original_store
            app_module.Settings.from_env = original_settings
            app_module.get_upload_storage = original_storage

        logs = "\n".join(captured.output)
        self.assertEqual(response.status_code, 200)
        self.assertIn("financial data has been received successfully", response.body.decode())
        self.assertNotIn(token, logs)
        self.assertNotIn("10000", logs)
        self.assertNotIn("client-private", logs)
        self.assertEqual(len(background_tasks.tasks), 1)

    def test_upload_route_failure_logs_no_sensitive_upload_data(self) -> None:
        for filename, content, storage in [
            ("client-validation-private.csv", csv_bytes(valid_dataframe().assign(Sales=["secret-value", 100, 200])), InMemoryUploadStorage()),
            ("client-storage-private.csv", csv_bytes(valid_dataframe()), InMemoryUploadStorage(fail_save=True)),
        ]:
            with self.subTest(filename=filename):
                store, order, token = paid_order_and_token()
                original_store = app_module.FirestoreOrderStore
                original_settings = app_module.Settings.from_env
                original_storage = app_module.get_upload_storage
                app_module.FirestoreOrderStore = lambda project=None: store
                app_module.Settings.from_env = settings
                app_module.get_upload_storage = lambda settings: storage
                upload_file = UploadFile(file=io.BytesIO(content), filename=filename)
                try:
                    with self.assertLogs("senalo.fulfilment", level=logging.INFO) as captured:
                        response = asyncio.run(
                            app_module.upload_submit(
                                session_request(store, order),
                                BackgroundTasks(),
                                t=None,
                                business_type="Food & Beverage",
                                opening_cash="123456",
                                financial_file=upload_file,
                            )
                        )
                finally:
                    app_module.FirestoreOrderStore = original_store
                    app_module.Settings.from_env = original_settings
                    app_module.get_upload_storage = original_storage

                logs = "\n".join(captured.output)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn(token, logs)
                self.assertNotIn("123456", logs)
                self.assertNotIn("secret-value", logs)
                self.assertNotIn(filename, logs)
                self.assertNotIn("Month,Sales", logs)
                self.assertNotIn("uploads/", logs)


class DummyRequest:
    def __init__(
        self,
        token: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        cookies: dict[str, str] | None = None,
    ):
        self.query_params = {"t": token} if token else {}
        self.headers = headers or {}
        self.cookies = cookies or {}


class JsonRequest(DummyRequest):
    def __init__(self, payload: dict):
        super().__init__()
        self.payload = payload

    async def json(self):
        return self.payload


if __name__ == "__main__":
    unittest.main()
