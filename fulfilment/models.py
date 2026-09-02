from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal


PaymentStatus = Literal["PENDING", "PAID", "FAILED", "REFUNDED"]
FulfilmentStatus = Literal[
    "NOT_STARTED",
    "AWAITING_UPLOAD",
    "VALIDATION_FAILED",
    "VALIDATED",
    "PROCESSING",
    "PROCESSING_FAILED",
    "READY",
]
EventStatus = Literal["PROCESSING", "PROCESSED", "FAILED"]
EmailStatus = Literal["NOT_SENT", "SENDING", "SENT", "FAILED"]
UploadStatus = Literal["NOT_UPLOADED", "UPLOADED", "SUPERSEDED"]
ValidationStatus = Literal["NOT_VALIDATED", "VALIDATED", "FAILED"]
AnalysisStatus = Literal["NOT_STARTED", "PROCESSING", "COMPLETED", "FAILED"]
ResultStatus = Literal["NOT_READY", "READY"]
ExpertReviewStatus = Literal["NOT_REQUIRED", "PENDING_REVIEW", "IN_REVIEW", "APPROVED", "RELEASED"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Order:
    order_id: str
    stripe_checkout_session_id: str
    stripe_payment_intent_id: str | None
    stripe_customer_id: str | None
    stripe_price_id: str | None
    stripe_product_id: str | None
    product_code: str
    amount_total: int | None
    currency: str | None
    customer_name: str | None
    customer_email: str | None
    payment_status: PaymentStatus
    fulfilment_status: FulfilmentStatus
    created_at: datetime
    updated_at: datetime
    paid_at: datetime | None = None
    customer_data_flags: list[str] = field(default_factory=list)
    token_hash: str | None = None
    token_seed: str | None = None
    token_version: int = 1
    token_created_at: datetime | None = None
    token_expires_at: datetime | None = None
    token_revoked_at: datetime | None = None
    email_status: EmailStatus = "NOT_SENT"
    email_provider_message_id: str | None = None
    email_sent_at: datetime | None = None
    email_last_error: str | None = None
    email_attempt_count: int = 0
    business_type: str | None = None
    opening_cash: float | None = None
    upload_status: UploadStatus = "NOT_UPLOADED"
    upload_object_path: str | None = None
    upload_original_filename: str | None = None
    upload_content_type: str | None = None
    upload_size_bytes: int | None = None
    upload_created_at: datetime | None = None
    validation_status: ValidationStatus = "NOT_VALIDATED"
    validation_error_code: str | None = None
    validated_at: datetime | None = None
    analysis_status: AnalysisStatus = "NOT_STARTED"
    analysis_started_at: datetime | None = None
    analysis_completed_at: datetime | None = None
    analysis_error_code: str | None = None
    pdf_object_path: str | None = None
    pdf_size_bytes: int | None = None
    excel_object_path: str | None = None
    excel_size_bytes: int | None = None
    result_status: ResultStatus = "NOT_READY"
    expert_review_status: ExpertReviewStatus = "NOT_REQUIRED"
    result_token_hash: str | None = None
    result_token_seed: str | None = None
    result_token_version: int = 1
    result_token_created_at: datetime | None = None
    result_token_expires_at: datetime | None = None
    result_token_revoked_at: datetime | None = None
    result_email_status: EmailStatus = "NOT_SENT"
    result_email_provider_message_id: str | None = None
    result_email_sent_at: datetime | None = None
    result_email_last_error: str | None = None
    result_email_attempt_count: int = 0
    delivered_at: datetime | None = None
    last_download_at: datetime | None = None
    download_count: int = 0
    review_commentary: str | None = None
    review_actions: list[dict[str, Any]] = field(default_factory=list)
    review_started_at: datetime | None = None
    review_updated_at: datetime | None = None
    approved_at: datetime | None = None
    released_at: datetime | None = None
    review_operator_id: str | None = None
    final_pdf_object_path: str | None = None
    final_pdf_size_bytes: int | None = None
    final_excel_object_path: str | None = None
    final_excel_size_bytes: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StripeEventRecord:
    event_id: str
    event_type: str
    received_at: datetime
    processing_status: EventStatus
    processed_at: datetime | None = None
    order_id: str | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
