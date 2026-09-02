from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .config import Settings
from .email_service import (
    EmailDeliveryError,
    EmailProvider,
    MissingCustomerEmailError,
    build_result_ready_email,
)
from .models import Order, utc_now
from .orders import OrderStore
from .storage import UploadStorage, UploadStorageError
from .tokens import TokenConfigurationError, TokenValidationError, hash_token, is_malformed_token, require_derivation_secret


logger = logging.getLogger("senalo.fulfilment")

RESULT_ACCESS_DENIED = "RESULT_ACCESS_DENIED"
RESULT_TOKEN_EXPIRED = "RESULT_TOKEN_EXPIRED"
RESULT_OBJECT_MISSING = "RESULT_OBJECT_MISSING"


class ResultAccessError(Exception):
    pass


class ResultTokenExpiredError(ResultAccessError):
    pass


class ResultNotReleasableError(ResultAccessError):
    pass


def derive_raw_result_token(token_seed: str, token_version: int, derivation_secret: str | None) -> str:
    secret = require_derivation_secret(derivation_secret)
    message = f"senalo-result-token:v{token_version}:{token_seed}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def result_token_expiry(now: datetime | None = None, days: int = 30) -> datetime:
    base = now or utc_now()
    return base + timedelta(days=days)


def attach_new_result_token(
    order: Order,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
    expiry_days: int = 30,
) -> tuple[Order, str]:
    from .tokens import generate_token_seed

    issued_at = now or utc_now()
    token_seed = generate_token_seed()
    token_version = (order.result_token_version or 0) + 1 if order.result_token_seed else 1
    raw_token = derive_raw_result_token(token_seed, token_version, derivation_secret)
    updated = replace(
        order,
        result_token_seed=token_seed,
        result_token_version=token_version,
        result_token_hash=hash_token(raw_token),
        result_token_created_at=issued_at,
        result_token_expires_at=result_token_expiry(issued_at, expiry_days),
        result_token_revoked_at=None,
        updated_at=issued_at,
    )
    return updated, raw_token


def reproduce_result_token(order: Order, derivation_secret: str | None) -> str:
    if not order.result_token_seed:
        raise TokenValidationError("Result token cannot be reproduced")
    return derive_raw_result_token(order.result_token_seed, order.result_token_version, derivation_secret)


def reissue_result_token(
    order_id: str,
    store: OrderStore,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
    expiry_days: int = 30,
) -> tuple[Order, str]:
    order = store.get_order(order_id)
    if not order:
        raise TokenValidationError("Result token cannot be reissued")
    revoked = replace(order, result_token_revoked_at=now or utc_now())
    updated, raw_token = attach_new_result_token(
        revoked,
        derivation_secret=derivation_secret,
        now=now,
        expiry_days=expiry_days,
    )
    return store.save_order(updated), raw_token


def validate_result_token(
    raw_token: str | None,
    store: OrderStore,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
) -> Order:
    require_derivation_secret(derivation_secret)
    if is_malformed_token(raw_token):
        raise TokenValidationError("Invalid result token")
    token_hash = hash_token(raw_token or "")
    order = store.get_order_by_result_token_hash(token_hash)
    if not order or not order.result_token_hash or not hmac.compare_digest(order.result_token_hash, token_hash):
        raise TokenValidationError("Invalid result token")

    current_time = now or utc_now()
    if order.result_token_revoked_at is not None:
        raise TokenValidationError("Invalid result token")
    expires_at = order.result_token_expires_at
    if expires_at is None:
        raise TokenValidationError("Invalid result token")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= current_time:
        raise ResultTokenExpiredError("Result token expired")
    return order


def build_secure_result_url(base_url: str, raw_token: str) -> str:
    return f"{base_url.rstrip('/')}/result?t={raw_token}"


def is_full_analysis_releasable(order: Order) -> bool:
    return (
        order.payment_status == "PAID"
        and order.product_code == "FULL_ANALYSIS"
        and order.result_status == "READY"
        and order.fulfilment_status == "READY"
        and bool(order.pdf_object_path)
        and bool(order.excel_object_path)
    )


def is_expert_review_releasable(order: Order) -> bool:
    return (
        order.payment_status == "PAID"
        and order.product_code == "EXPERT_REVIEW"
        and order.result_status == "READY"
        and order.fulfilment_status == "READY"
        and order.expert_review_status == "RELEASED"
        and bool(order.final_pdf_object_path)
        and bool(order.final_excel_object_path)
    )


def is_result_releasable(order: Order) -> bool:
    return is_full_analysis_releasable(order) or is_expert_review_releasable(order)


def is_expert_review_blocked(order: Order) -> bool:
    return order.product_code == "EXPERT_REVIEW" and not is_expert_review_releasable(order)


def result_product_label(order: Order) -> str:
    if order.product_code == "EXPERT_REVIEW":
        return "SENALO Expert Review"
    return "SENALO Full Analysis"


def mark_result_accessed(order: Order, store: OrderStore, now: datetime | None = None) -> Order:
    accessed_at = now or utc_now()
    updated = replace(
        order,
        delivered_at=order.delivered_at or accessed_at,
        updated_at=accessed_at,
    )
    return store.save_order(updated)


def record_download(order: Order, store: OrderStore, now: datetime | None = None) -> Order:
    downloaded_at = now or utc_now()
    updated = replace(
        order,
        delivered_at=order.delivered_at or downloaded_at,
        last_download_at=downloaded_at,
        download_count=order.download_count + 1,
        updated_at=downloaded_at,
    )
    return store.save_order(updated)


def load_authorized_artifact(
    *,
    raw_token: str | None,
    artifact_type: str,
    store: OrderStore,
    storage: UploadStorage,
    derivation_secret: str | None,
) -> tuple[Order, bytes, str, str]:
    order = validate_result_token(raw_token, store, derivation_secret=derivation_secret)
    if not is_result_releasable(order):
        raise ResultNotReleasableError(RESULT_ACCESS_DENIED)
    if artifact_type == "pdf":
        object_path = order.final_pdf_object_path if order.product_code == "EXPERT_REVIEW" else order.pdf_object_path
        content_type = "application/pdf"
        filename = "SENALO-Expert-Review.pdf" if order.product_code == "EXPERT_REVIEW" else "SENALO-Full-Analysis.pdf"
    elif artifact_type == "excel":
        object_path = order.final_excel_object_path if order.product_code == "EXPERT_REVIEW" else order.excel_object_path
        content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = "SENALO-Expert-Review.xlsx" if order.product_code == "EXPERT_REVIEW" else "SENALO-Full-Analysis.xlsx"
    else:
        raise ResultNotReleasableError(RESULT_ACCESS_DENIED)
    try:
        content = storage.load(str(object_path))
    except UploadStorageError as exc:
        raise ResultNotReleasableError(RESULT_OBJECT_MISSING) from exc
    saved = record_download(order, store)
    logger.info(
        "result_artifact_downloaded",
        extra={
            "order_id": saved.order_id,
            "artifact_type": artifact_type,
            "download_count": saved.download_count,
        },
    )
    return saved, content, content_type, filename


class ResultDeliveryService:
    def __init__(self, store: OrderStore, settings: Settings, email_provider: EmailProvider) -> None:
        self.store = store
        self.settings = settings
        self.email_provider = email_provider

    def send_result_ready_email(self, order_id: str) -> Order:
        started = time.perf_counter()
        order = self.store.get_order(order_id)
        if not order:
            raise EmailDeliveryError("ORDER_NOT_FOUND")
        if not is_result_releasable(order):
            return order
        if order.result_email_status == "SENT":
            return order

        try:
            if not order.result_token_hash or not order.result_token_seed or order.result_token_revoked_at:
                with_token, raw_token = attach_new_result_token(
                    order,
                    derivation_secret=self.settings.token_derivation_secret,
                    expiry_days=self.settings.result_token_expiry_days,
                )
                self.store.save_order(with_token)
            else:
                with_token = order
                raw_token = reproduce_result_token(order, self.settings.token_derivation_secret)
        except (TokenConfigurationError, TokenValidationError) as exc:
            failed = replace(
                order,
                result_email_status="FAILED",
                result_email_last_error=str(exc) or exc.__class__.__name__,
                updated_at=utc_now(),
            )
            return self.store.save_order(failed)

        claimed = self.store.claim_result_email_send(with_token.order_id, utc_now())
        if not claimed:
            return self.store.get_order(with_token.order_id) or with_token

        secure_url = build_secure_result_url(self.settings.senalo_public_fulfilment_base_url, raw_token)
        try:
            email = build_result_ready_email(claimed, secure_url, self.settings.senalo_email_reply_to)
            result = self.email_provider.send(email, idempotency_key=f"result-ready/{claimed.order_id}")
        except MissingCustomerEmailError:
            failed = replace(
                claimed,
                result_email_status="FAILED",
                result_email_last_error="MISSING_CUSTOMER_EMAIL",
                updated_at=utc_now(),
            )
            logger.warning("result_email_failed", extra={"order_id": claimed.order_id, "error_code": "MISSING_CUSTOMER_EMAIL"})
            return self.store.save_order(failed)
        except EmailDeliveryError as exc:
            failed = replace(
                claimed,
                result_email_status="FAILED",
                result_email_last_error=str(exc) or exc.__class__.__name__,
                updated_at=utc_now(),
            )
            logger.warning("result_email_failed", extra={"order_id": claimed.order_id, "error_code": failed.result_email_last_error})
            return self.store.save_order(failed)

        sent_at = utc_now()
        sent = replace(
            claimed,
            result_email_status="SENT",
            result_email_provider_message_id=result.provider_message_id,
            result_email_sent_at=sent_at,
            result_email_last_error=None,
            updated_at=sent_at,
        )
        logger.info(
            "result_email_sent",
            extra={
                "order_id": claimed.order_id,
                "result_email_status": "SENT",
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return self.store.save_order(sent)
