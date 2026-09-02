from __future__ import annotations

import logging
import unittest
import io
from dataclasses import replace
from pathlib import Path

import openpyxl

import fulfilment.analysis_processor as processor_module
from fulfilment.analysis_processor import (
    EXCEL_GENERATION_FAILED,
    INPUT_REVALIDATION_FAILED,
    INVALID_PERSISTED_INTAKE,
    PDF_GENERATION_FAILED,
    PROCESSING_NOT_CLAIMED,
    RESULT_STORAGE_FAILED,
    SOURCE_FILE_MISSING,
    SOURCE_FILE_UNREADABLE,
    process_order_analysis,
)
from fulfilment.email_service import RecordingEmailProvider
from fulfilment.fulfilment_service import OrderFulfilmentService
from fulfilment.orders import InMemoryOrderStore, process_stripe_event
from fulfilment.storage import InMemoryUploadStorage, UploadStorageError
from fulfilment.upload_intake import submit_upload
from tests.test_fulfilment_gate2 import event, session
from tests.test_fulfilment_gate4 import csv_bytes, legacy_dataframe, settings, valid_dataframe, xlsx_bytes


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ResultFailingStorage(InMemoryUploadStorage):
    def __init__(self, fail_on_result_number: int) -> None:
        super().__init__()
        self.fail_on_result_number = fail_on_result_number
        self.result_count = 0

    def save_result(self, *, order_id: str, content: bytes, extension: str, content_type: str):
        self.result_count += 1
        if self.result_count == self.fail_on_result_number:
            raise UploadStorageError("RESULT_STORAGE_SAVE_FAILED")
        return super().save_result(
            order_id=order_id,
            content=content,
            extension=extension,
            content_type=content_type,
        )


class FailOnceReadySaveStore(InMemoryOrderStore):
    def __init__(self) -> None:
        super().__init__()
        self.failed_ready_save = False

    def save_order(self, order):
        if order.fulfilment_status == "READY" and not self.failed_ready_save:
            self.failed_ready_save = True
            raise RuntimeError("order update failed")
        return super().save_order(order)


class Gate5AnalysisProcessingTests(unittest.TestCase):
    def create_validated_order(
        self,
        *,
        product_code: str = "FULL_ANALYSIS",
        filename: str = "financials.csv",
        content: bytes | None = None,
        business_type: str = "Food & Beverage",
        opening_cash: str = "100000",
        store: InMemoryOrderStore | None = None,
        storage: InMemoryUploadStorage | None = None,
    ):
        store = store or InMemoryOrderStore()
        storage = storage or InMemoryUploadStorage()
        email_provider = RecordingEmailProvider()
        service = OrderFulfilmentService(store, settings(), email_provider)
        if product_code == "EXPERT_REVIEW":
            checkout_session = session(
                session_id="cs_test_expert_gate5",
                price_id="price_expert_test",
                product_id="prod_expert_test",
                amount_total=14900,
                metadata_code="EXPERT_REVIEW",
            )
        else:
            checkout_session = session(session_id="cs_test_full_gate5")
        process_stripe_event(
            event(f"evt_{product_code}_{len(store.orders)}", "checkout.session.completed", checkout_session),
            store,
            settings(),
            fulfilment_service=service,
        )
        order = next(iter(store.orders.values()))
        result = submit_upload(
            order=order,
            store=store,
            storage=storage,
            business_type=business_type,
            opening_cash_value=opening_cash,
            filename=filename,
            content=content if content is not None else csv_bytes(valid_dataframe()),
            max_upload_bytes=settings().max_upload_bytes,
        )
        return store, storage, result.order

    def test_full_analysis_csv_xlsx_and_legacy_orders_reach_ready(self) -> None:
        cases = [
            ("financials.csv", csv_bytes(valid_dataframe())),
            ("financials.xlsx", xlsx_bytes(valid_dataframe())),
            ("legacy.csv", csv_bytes(legacy_dataframe())),
        ]
        for filename, content in cases:
            with self.subTest(filename=filename):
                store, storage, order = self.create_validated_order(filename=filename, content=content)
                result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
                self.assertEqual(result.status, "READY")
                self.assertEqual(result.order.fulfilment_status, "READY")
                self.assertEqual(result.order.analysis_status, "COMPLETED")
                self.assertEqual(result.order.result_status, "READY")
                self.assertEqual(result.order.expert_review_status, "NOT_REQUIRED")
                self.assertIn(result.order.pdf_object_path, storage.objects)
                self.assertIn(result.order.excel_object_path, storage.objects)
                self.assertTrue(result.artifacts.pdf.startswith(b"%PDF-1.4"))
                self.assertGreater(result.order.pdf_size_bytes, 1000)
                self.assertGreater(result.order.excel_size_bytes, 1000)

    def test_financial_benchmarks_and_scenario_ratios_are_preserved(self) -> None:
        expectations = [
            ("healthy_food_business.csv", "Food & Beverage", 100, "Healthy"),
            ("watch_food_business.csv", "Food & Beverage", 42, "Watch"),
            ("at_risk_food_business.csv", "Food & Beverage", 0, "At Risk"),
            ("healthy_market_vendor.csv", "Market Stall / Vendor", 100, "Healthy"),
            ("watch_market_vendor.csv", "Market Stall / Vendor", 45, "Watch"),
            ("sample_data.csv", "Food & Beverage", 90, "Healthy"),
        ]
        for filename, business_type, score, label in expectations:
            with self.subTest(filename=filename):
                content = (PROJECT_ROOT / filename).read_bytes()
                store, storage, order = self.create_validated_order(
                    filename=filename,
                    content=content,
                    business_type=business_type,
                    opening_cash="100000",
                )
                result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
                self.assertEqual(result.artifacts.score, score)
                self.assertEqual(result.artifacts.score_label, label)
                self.assertEqual(len(result.artifacts.management_priorities), 3)
                base = float(result.artifacts.scenarios.loc[0, "Sales"])
                downside = float(result.artifacts.scenarios.loc[1, "Sales"])
                upside = float(result.artifacts.scenarios.loc[2, "Sales"])
                self.assertAlmostEqual(downside / base, 0.85, places=6)
                self.assertAlmostEqual(upside / base, 1.15, places=6)

    def test_pdf_contains_sections_and_excel_contains_expected_sheets(self) -> None:
        store, storage, order = self.create_validated_order()
        result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        pdf = storage.objects[result.order.pdf_object_path]
        self.assertIn(b"Business Financial Health Report", pdf)
        self.assertIn(b"12-Month Forecast and Scenario Analysis", pdf)
        self.assertIn(b"Management Priorities", pdf)

        excel_path = result.order.excel_object_path
        workbook = openpyxl.load_workbook(filename=io.BytesIO(storage.objects[excel_path]), read_only=True)
        self.assertEqual(
            set(workbook.sheetnames),
            {"Historical Analysis", "12-Month Forecast", "Scenario Analysis", "Health Score", "Assumptions"},
        )
        workbook.close()

    def test_opening_cash_and_business_type_are_propagated(self) -> None:
        store, storage, order = self.create_validated_order(
            business_type="Market Stall / Vendor",
            opening_cash="12345",
        )
        result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(result.order.business_type, "Market Stall / Vendor")
        self.assertEqual(result.order.opening_cash, 12345)
        self.assertAlmostEqual(result.artifacts.metrics["cash_balance"], 19695)

    def test_expert_review_base_analysis_ready_but_pending_manual_review(self) -> None:
        store, storage, order = self.create_validated_order(product_code="EXPERT_REVIEW")
        result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(result.status, "READY")
        self.assertEqual(result.order.fulfilment_status, "READY")
        self.assertEqual(result.order.result_status, "READY")
        self.assertEqual(result.order.expert_review_status, "PENDING_REVIEW")
        self.assertIsNotNone(result.order.pdf_object_path)
        self.assertIsNotNone(result.order.excel_object_path)

    def test_processing_claim_prevents_duplicates_and_ready_reprocessing(self) -> None:
        store, storage, order = self.create_validated_order()
        claimed = store.claim_analysis_processing(order.order_id, order.updated_at)
        self.assertIsNotNone(claimed)
        duplicate = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(duplicate.status, PROCESSING_NOT_CLAIMED)
        self.assertIsNone(duplicate.artifacts)

        store, storage, order = self.create_validated_order()
        ready = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        second = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(ready.status, "READY")
        self.assertEqual(second.status, PROCESSING_NOT_CLAIMED)
        self.assertEqual(len([path for path in storage.objects if path.startswith("results/")]), 2)

    def test_processing_failed_can_retry(self) -> None:
        store, storage, order = self.create_validated_order()
        first = process_order_analysis(
            order_id=order.order_id,
            store=store,
            storage=storage,
            pdf_builder=lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("pdf failed")),
        )
        self.assertEqual(first.status, PDF_GENERATION_FAILED)
        self.assertEqual(first.order.fulfilment_status, "PROCESSING_FAILED")
        retry_blocked = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(retry_blocked.status, PROCESSING_NOT_CLAIMED)
        retry = process_order_analysis(order_id=order.order_id, store=store, storage=storage, retry_failed=True)
        self.assertEqual(retry.status, "READY")

    def test_failure_paths_do_not_mark_ready_or_release_partial_results(self) -> None:
        cases = [
            ("missing_source", lambda store, storage, order: (storage.objects.pop(order.upload_object_path), {}), SOURCE_FILE_MISSING),
            (
                "unreadable_source",
                lambda store, storage, order: (
                    (
                        store.save_order(replace(order, upload_original_filename="bad.xlsx")),
                        storage.objects.__setitem__(order.upload_object_path, b"not a zip"),
                    ),
                    {},
                ),
                SOURCE_FILE_UNREADABLE,
            ),
            ("invalid_persisted_intake", lambda store, storage, order: (store.save_order(replace(order, business_type=None)), {}), INVALID_PERSISTED_INTAKE),
            ("pdf_generation", lambda store, storage, order: (None, {"pdf_builder": failing_builder("pdf")}), PDF_GENERATION_FAILED),
            ("excel_generation", lambda store, storage, order: (None, {"excel_builder": failing_builder("excel")}), EXCEL_GENERATION_FAILED),
        ]
        for name, mutate, expected_status in cases:
            with self.subTest(name=name):
                store, storage, order = self.create_validated_order()
                _, kwargs = mutate(store, storage, order)
                result = process_order_analysis(order_id=order.order_id, store=store, storage=storage, **kwargs)
                expected = expected_status
                self.assertEqual(result.status, expected)
                self.assertEqual(result.order.fulfilment_status, "PROCESSING_FAILED")
                self.assertEqual(result.order.result_status, "NOT_READY")
                self.assertIsNone(result.order.pdf_object_path)
                self.assertIsNone(result.order.excel_object_path)

    def test_analysis_exception_marks_processing_failed(self) -> None:
        store, storage, order = self.create_validated_order()
        original = processor_module.calculate_financials
        processor_module.calculate_financials = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("analysis failed"))
        try:
            result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        finally:
            processor_module.calculate_financials = original
        self.assertEqual(result.status, "ANALYSIS_FAILED")
        self.assertEqual(result.order.fulfilment_status, "PROCESSING_FAILED")
        self.assertEqual(result.order.result_status, "NOT_READY")

    def test_result_storage_failure_cleans_partial_pdf_and_order_update_failure_is_recoverable(self) -> None:
        store, storage, order = self.create_validated_order(storage=ResultFailingStorage(fail_on_result_number=2))
        result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(result.status, RESULT_STORAGE_FAILED)
        self.assertEqual(result.order.fulfilment_status, "PROCESSING_FAILED")
        self.assertEqual([path for path in storage.objects if path.startswith("results/")], [])
        self.assertEqual(len([path for path in storage.deleted if path.startswith("results/")]), 1)

        store = FailOnceReadySaveStore()
        storage = InMemoryUploadStorage()
        _, storage, order = self.create_validated_order(store=store, storage=storage)
        result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        self.assertEqual(result.status, "ORDER_UPDATE_FAILED")
        self.assertEqual([path for path in storage.objects if path.startswith("results/")], [])

    def test_order_data_logs_and_object_names_do_not_include_sensitive_content(self) -> None:
        store, storage, order = self.create_validated_order(filename="Jane Owner private finances.csv")
        with self.assertLogs("senalo.fulfilment", level=logging.INFO) as captured:
            result = process_order_analysis(order_id=order.order_id, store=store, storage=storage)
        order_text = str(
            {
                key: value
                for key, value in result.order.to_dict().items()
                if key
                not in {
                    "customer_name",
                    "customer_email",
                    "opening_cash",
                    "upload_original_filename",
                    "token_hash",
                    "token_seed",
                }
            }
        )
        logs = "\n".join(captured.output)
        result_paths = [result.order.pdf_object_path, result.order.excel_object_path]
        for forbidden in [
            "10000",
            "Sales,Direct",
            "Jane Owner",
            "jane@example.com",
            "100000",
            "/upload?t=",
        ]:
            self.assertNotIn(forbidden, order_text)
            self.assertNotIn(forbidden, logs)
            self.assertTrue(all(forbidden not in path for path in result_paths))
        self.assertNotIn("Jane Owner", logs)
        self.assertNotIn("jane@example.com", logs)
        self.assertTrue(all("Jane Owner" not in path for path in result_paths))
        self.assertTrue(all("jane@example.com" not in path for path in result_paths))
        self.assertTrue(all(path.startswith(f"results/{order.order_id}/") for path in result_paths))


def failing_builder(name: str):
    def builder(*args, **kwargs):
        raise RuntimeError(f"{name} failed")

    return builder

if __name__ == "__main__":
    unittest.main()
