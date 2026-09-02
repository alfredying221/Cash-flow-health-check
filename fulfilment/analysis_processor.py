from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import pandas as pd

from senalo_analysis import (
    BUSINESS_TYPES,
    DEFAULT_DOWNSIDE_ADJUSTMENT,
    DEFAULT_UPSIDE_ADJUSTMENT,
    build_forecast,
    build_report_pdf,
    build_scenario_from_base,
    calculate_financials,
    export_excel,
    make_cfo_summary,
    make_management_priorities,
    read_uploaded_file,
    safe_divide,
    score_cash_health,
    summarize_metrics,
    validate_and_prepare,
)

from .models import Order, utc_now
from .orders import OrderStore
from .storage import UploadStorage, UploadStorageError
from .upload_intake import NamedBytesIO, detect_upload_type, parse_opening_cash, reject_formula_cells


logger = logging.getLogger("senalo.fulfilment")

SOURCE_FILE_MISSING = "SOURCE_FILE_MISSING"
SOURCE_FILE_UNREADABLE = "SOURCE_FILE_UNREADABLE"
INPUT_REVALIDATION_FAILED = "INPUT_REVALIDATION_FAILED"
ANALYSIS_FAILED = "ANALYSIS_FAILED"
PDF_GENERATION_FAILED = "PDF_GENERATION_FAILED"
EXCEL_GENERATION_FAILED = "EXCEL_GENERATION_FAILED"
RESULT_STORAGE_FAILED = "RESULT_STORAGE_FAILED"
ORDER_UPDATE_FAILED = "ORDER_UPDATE_FAILED"
INVALID_PERSISTED_INTAKE = "INVALID_PERSISTED_INTAKE"
PROCESSING_NOT_CLAIMED = "PROCESSING_NOT_CLAIMED"


class AnalysisProcessingError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


@dataclass(frozen=True)
class AnalysisArtifacts:
    pdf: bytes
    excel: bytes
    metrics: dict[str, float]
    score: int
    score_label: str
    management_priorities: list[str]
    scenarios: pd.DataFrame


@dataclass(frozen=True)
class ProcessingResult:
    order: Order
    artifacts: AnalysisArtifacts | None
    status: str


def process_order_analysis(
    *,
    order_id: str,
    store: OrderStore,
    storage: UploadStorage,
    retry_failed: bool = False,
    pdf_builder: Callable[..., bytes] = build_report_pdf,
    excel_builder: Callable[..., bytes] = export_excel,
) -> ProcessingResult:
    started = time.perf_counter()
    claimed = store.claim_analysis_processing(order_id, utc_now(), retry_failed=retry_failed)
    if claimed is None:
        order = store.get_order(order_id)
        return ProcessingResult(order=order, artifacts=None, status=PROCESSING_NOT_CLAIMED)

    result_paths: list[str] = []
    try:
        artifacts = build_analysis_artifacts(claimed, storage, pdf_builder=pdf_builder, excel_builder=excel_builder)
        try:
            pdf_result = storage.save_result(
                order_id=claimed.order_id,
                content=artifacts.pdf,
                extension=".pdf",
                content_type="application/pdf",
            )
        except Exception as exc:
            raise AnalysisProcessingError(RESULT_STORAGE_FAILED) from exc
        result_paths.append(pdf_result.object_path)
        try:
            excel_result = storage.save_result(
                order_id=claimed.order_id,
                content=artifacts.excel,
                extension=".xlsx",
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        except Exception as exc:
            raise AnalysisProcessingError(RESULT_STORAGE_FAILED) from exc
        result_paths.append(excel_result.object_path)

        completed_at = utc_now()
        ready = replace(
            claimed,
            fulfilment_status="READY",
            analysis_status="COMPLETED",
            analysis_completed_at=completed_at,
            analysis_error_code=None,
            pdf_object_path=pdf_result.object_path,
            pdf_size_bytes=pdf_result.size_bytes,
            excel_object_path=excel_result.object_path,
            excel_size_bytes=excel_result.size_bytes,
            result_status="READY",
            expert_review_status="PENDING_REVIEW"
            if claimed.product_code == "EXPERT_REVIEW"
            else "NOT_REQUIRED",
            updated_at=completed_at,
        )
        try:
            saved = store.save_order(ready)
        except Exception as exc:
            cleanup_objects(storage, result_paths)
            raise AnalysisProcessingError(ORDER_UPDATE_FAILED) from exc

        cleanup_previous_results(storage, claimed, saved)
        logger.info(
            "analysis_processed",
            extra={
                "order_id": saved.order_id,
                "product_code": saved.product_code,
                "processing_state": saved.fulfilment_status,
                "result_status": saved.result_status,
                "pdf_size": saved.pdf_size_bytes,
                "excel_size": saved.excel_size_bytes,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return ProcessingResult(order=saved, artifacts=artifacts, status="READY")
    except AnalysisProcessingError as exc:
        cleanup_objects(storage, result_paths)
        failed = mark_processing_failed(claimed, store, exc.error_code)
        logger.warning(
            "analysis_processing_failed",
            extra={
                "order_id": claimed.order_id,
                "product_code": claimed.product_code,
                "processing_state": "PROCESSING_FAILED",
                "error_code": exc.error_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return ProcessingResult(order=failed, artifacts=None, status=exc.error_code)
    except Exception as exc:
        cleanup_objects(storage, result_paths)
        failed = mark_processing_failed(claimed, store, ANALYSIS_FAILED)
        logger.warning(
            "analysis_processing_failed",
            extra={
                "order_id": claimed.order_id,
                "product_code": claimed.product_code,
                "processing_state": "PROCESSING_FAILED",
                "error_code": ANALYSIS_FAILED,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return ProcessingResult(order=failed, artifacts=None, status=ANALYSIS_FAILED)


def build_analysis_artifacts(
    order: Order,
    storage: UploadStorage,
    *,
    pdf_builder: Callable[..., bytes] = build_report_pdf,
    excel_builder: Callable[..., bytes] = export_excel,
) -> AnalysisArtifacts:
    validate_persisted_order(order)
    source_bytes = load_source_bytes(order, storage)
    prepared = parse_source_file(order, source_bytes)

    try:
        financials = calculate_financials(prepared, float(order.opening_cash))
        metrics = summarize_metrics(financials)
        score, score_label, score_breakdown = score_cash_health(financials, metrics)
        forecast_assumptions = default_forecast_assumptions(metrics)
        forecast = build_forecast(
            financials,
            metrics["cash_balance"],
            forecast_assumptions["Forecast Sales Growth"],
            forecast_assumptions["Direct Costs %"],
            forecast_assumptions["Labour Cost Growth"],
            forecast_assumptions["Occupancy Cost Growth"],
            forecast_assumptions["Other Operating Costs Growth"],
        )
        forecast_metrics = summarize_metrics(forecast)
        scenario_details, scenarios = build_scenarios(
            forecast,
            metrics["cash_balance"],
            forecast_assumptions["Direct Costs %"],
        )
        downside_metrics = scenario_details["Downside Case"]["metrics"]
        summary_lines = make_cfo_summary(financials, metrics, score_label, downside_metrics)
        management_priorities = make_management_priorities(financials, metrics, downside_metrics)
    except Exception as exc:
        raise AnalysisProcessingError(ANALYSIS_FAILED) from exc

    assumptions = {
        "Business Type": order.business_type,
        **forecast_assumptions,
        "Downside Sales Adjustment": DEFAULT_DOWNSIDE_ADJUSTMENT,
        "Upside Sales Adjustment": DEFAULT_UPSIDE_ADJUSTMENT,
    }
    try:
        pdf = pdf_builder(
            metrics,
            score,
            score_label,
            forecast_metrics,
            scenarios,
            summary_lines,
            str(order.business_type),
            management_priorities,
            assumptions,
        )
    except Exception as exc:
        raise AnalysisProcessingError(PDF_GENERATION_FAILED) from exc

    try:
        excel = excel_builder(
            financials,
            forecast,
            scenarios,
            score_breakdown,
            assumptions,
            str(order.business_type),
            scenario_details,
        )
    except Exception as exc:
        raise AnalysisProcessingError(EXCEL_GENERATION_FAILED) from exc

    return AnalysisArtifacts(
        pdf=pdf,
        excel=excel,
        metrics=metrics,
        score=score,
        score_label=score_label,
        management_priorities=management_priorities,
        scenarios=scenarios,
    )


def validate_persisted_order(order: Order) -> None:
    if order.payment_status != "PAID":
        raise AnalysisProcessingError(INVALID_PERSISTED_INTAKE)
    if order.product_code not in {"FULL_ANALYSIS", "EXPERT_REVIEW"}:
        raise AnalysisProcessingError(INVALID_PERSISTED_INTAKE)
    if order.business_type not in BUSINESS_TYPES:
        raise AnalysisProcessingError(INVALID_PERSISTED_INTAKE)
    try:
        parse_opening_cash(order.opening_cash)
    except Exception as exc:
        raise AnalysisProcessingError(INVALID_PERSISTED_INTAKE) from exc
    if order.upload_status != "UPLOADED" or order.validation_status != "VALIDATED":
        raise AnalysisProcessingError(INVALID_PERSISTED_INTAKE)
    if not order.upload_object_path:
        raise AnalysisProcessingError(SOURCE_FILE_MISSING)


def load_source_bytes(order: Order, storage: UploadStorage) -> bytes:
    try:
        return storage.load(str(order.upload_object_path))
    except UploadStorageError as exc:
        raise AnalysisProcessingError(SOURCE_FILE_MISSING) from exc
    except Exception as exc:
        raise AnalysisProcessingError(SOURCE_FILE_UNREADABLE) from exc


def parse_source_file(order: Order, source_bytes: bytes) -> pd.DataFrame:
    filename = order.upload_original_filename or Path(str(order.upload_object_path)).name
    try:
        extension, _ = detect_upload_type(filename, source_bytes)
        reject_formula_cells(filename, source_bytes, extension)
        dataframe = read_uploaded_file(NamedBytesIO(source_bytes, filename))
    except AnalysisProcessingError:
        raise
    except Exception as exc:
        raise AnalysisProcessingError(SOURCE_FILE_UNREADABLE) from exc

    prepared, errors = validate_and_prepare(dataframe)
    if errors or prepared is None:
        raise AnalysisProcessingError(INPUT_REVALIDATION_FAILED)
    return prepared


def default_forecast_assumptions(metrics: dict[str, float]) -> dict[str, float]:
    return {
        "Forecast Sales Growth": 0.0,
        "Direct Costs %": metrics["direct_costs_percentage"],
        "Labour Cost Growth": 0.0,
        "Occupancy Cost Growth": 0.0,
        "Other Operating Costs Growth": 0.0,
    }


def build_scenarios(
    forecast: pd.DataFrame,
    starting_cash: float,
    direct_costs_percentage: float,
) -> tuple[dict[str, dict[str, pd.DataFrame | dict[str, float | str]]], pd.DataFrame]:
    scenario_details: dict[str, dict[str, pd.DataFrame | dict[str, float | str]]] = {}
    rows = []
    for scenario_name, adjustment in [
        ("Base Case", 0.0),
        ("Downside Case", DEFAULT_DOWNSIDE_ADJUSTMENT),
        ("Upside Case", DEFAULT_UPSIDE_ADJUSTMENT),
    ]:
        scenario_forecast, scenario_metrics = build_scenario_from_base(
            forecast,
            starting_cash,
            scenario_name,
            adjustment,
            direct_costs_percentage,
        )
        scenario_details[scenario_name] = {
            "forecast": scenario_forecast,
            "metrics": summarize_metrics(scenario_forecast),
        }
        rows.append(scenario_metrics)
    return scenario_details, pd.DataFrame(rows)


def mark_processing_failed(order: Order, store: OrderStore, error_code: str) -> Order:
    failed_at = utc_now()
    failed = replace(
        order,
        fulfilment_status="PROCESSING_FAILED",
        analysis_status="FAILED",
        analysis_error_code=error_code,
        analysis_completed_at=failed_at,
        result_status="NOT_READY",
        updated_at=failed_at,
    )
    return store.save_order(failed)


def cleanup_objects(storage: UploadStorage, object_paths: list[str]) -> None:
    for object_path in object_paths:
        try:
            storage.delete(object_path)
        except Exception:
            pass


def cleanup_previous_results(storage: UploadStorage, previous: Order, current: Order) -> None:
    for object_path in [previous.pdf_object_path, previous.excel_object_path]:
        if object_path and object_path not in {current.pdf_object_path, current.excel_object_path}:
            try:
                storage.delete(object_path)
            except Exception:
                pass
