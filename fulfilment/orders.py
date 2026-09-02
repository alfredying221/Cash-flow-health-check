from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol

from .config import ProductConfig, Settings
from .models import CustomerSession, Order, StripeEventRecord, utc_now


class FulfilmentError(Exception):
    status_code = 500
    error_code = "FULFILMENT_ERROR"


class ProductMappingError(FulfilmentError):
    status_code = 422
    error_code = "PRODUCT_MAPPING_ERROR"


class ProductConflictError(FulfilmentError):
    status_code = 422
    error_code = "PRODUCT_CONFLICT"


class AmountMismatchError(FulfilmentError):
    status_code = 422
    error_code = "AMOUNT_MISMATCH"


class MissingCheckoutSessionError(FulfilmentError):
    status_code = 422
    error_code = "MISSING_CHECKOUT_SESSION"


class OrderStore(Protocol):
    def reserve_event(self, event_id: str, event_type: str, received_at: datetime) -> str:
        """Return reserved, duplicate_success, or duplicate_processing."""

    def mark_event_processed(self, event_id: str, order_id: str, processed_at: datetime) -> None:
        ...

    def mark_event_failed(self, event_id: str, error_code: str, processed_at: datetime) -> None:
        ...

    def upsert_order(self, order: Order) -> Order:
        ...

    def get_order_by_checkout_session(self, checkout_session_id: str) -> Order | None:
        ...

    def get_order(self, order_id: str) -> Order | None:
        ...

    def list_orders(self) -> list[Order]:
        ...

    def get_order_by_token_hash(self, token_hash: str) -> Order | None:
        ...

    def get_order_by_result_token_hash(self, token_hash: str) -> Order | None:
        ...

    def save_order(self, order: Order) -> Order:
        ...

    def claim_email_send(self, order_id: str, claimed_at: datetime) -> Order | None:
        ...

    def claim_analysis_processing(self, order_id: str, claimed_at: datetime, retry_failed: bool = False) -> Order | None:
        ...

    def claim_result_email_send(self, order_id: str, claimed_at: datetime) -> Order | None:
        ...

    def claim_expert_review_release(self, order_id: str, released_at: datetime) -> Order | None:
        ...

    def save_customer_session(self, session: CustomerSession) -> CustomerSession:
        ...

    def get_customer_session(self, session_hash: str) -> CustomerSession | None:
        ...

    def delete_customer_session(self, session_hash: str) -> None:
        ...


class InMemoryOrderStore:
    def __init__(self) -> None:
        self.events: dict[str, StripeEventRecord] = {}
        self.orders: dict[str, Order] = {}
        self.checkout_index: dict[str, str] = {}
        self.customer_sessions: dict[str, CustomerSession] = {}

    def reserve_event(self, event_id: str, event_type: str, received_at: datetime) -> str:
        existing = self.events.get(event_id)
        if existing and existing.processing_status == "PROCESSED":
            return "duplicate_success"
        if existing and existing.processing_status == "PROCESSING":
            return "duplicate_processing"
        self.events[event_id] = StripeEventRecord(
            event_id=event_id,
            event_type=event_type,
            received_at=received_at,
            processing_status="PROCESSING",
        )
        return "reserved"

    def mark_event_processed(self, event_id: str, order_id: str, processed_at: datetime) -> None:
        event = self.events[event_id]
        self.events[event_id] = replace(
            event,
            processing_status="PROCESSED",
            processed_at=processed_at,
            order_id=order_id,
            error_code=None,
        )

    def mark_event_failed(self, event_id: str, error_code: str, processed_at: datetime) -> None:
        event = self.events[event_id]
        self.events[event_id] = replace(
            event,
            processing_status="FAILED",
            processed_at=processed_at,
            error_code=error_code,
        )

    def upsert_order(self, order: Order) -> Order:
        existing = self.orders.get(order.order_id)
        if existing:
            created_at = existing.created_at
            customer_data_flags = order.customer_data_flags or existing.customer_data_flags
            order = replace(
                order,
                created_at=created_at,
                customer_data_flags=customer_data_flags,
                token_hash=existing.token_hash,
                token_seed=existing.token_seed,
                token_version=existing.token_version,
                token_created_at=existing.token_created_at,
                token_expires_at=existing.token_expires_at,
                token_revoked_at=existing.token_revoked_at,
                email_status=existing.email_status,
                email_provider_message_id=existing.email_provider_message_id,
                email_sent_at=existing.email_sent_at,
                email_last_error=existing.email_last_error,
                email_attempt_count=existing.email_attempt_count,
                fulfilment_status=existing.fulfilment_status,
                business_type=existing.business_type,
                opening_cash=existing.opening_cash,
                upload_status=existing.upload_status,
                upload_object_path=existing.upload_object_path,
                upload_original_filename=existing.upload_original_filename,
                upload_content_type=existing.upload_content_type,
                upload_size_bytes=existing.upload_size_bytes,
                upload_created_at=existing.upload_created_at,
                validation_status=existing.validation_status,
                validation_error_code=existing.validation_error_code,
                validated_at=existing.validated_at,
                analysis_status=existing.analysis_status,
                analysis_started_at=existing.analysis_started_at,
                analysis_completed_at=existing.analysis_completed_at,
                analysis_error_code=existing.analysis_error_code,
                pdf_object_path=existing.pdf_object_path,
                pdf_size_bytes=existing.pdf_size_bytes,
                excel_object_path=existing.excel_object_path,
                excel_size_bytes=existing.excel_size_bytes,
                result_status=existing.result_status,
                expert_review_status=existing.expert_review_status,
                result_token_hash=existing.result_token_hash,
                result_token_seed=existing.result_token_seed,
                result_token_version=existing.result_token_version,
                result_token_created_at=existing.result_token_created_at,
                result_token_expires_at=existing.result_token_expires_at,
                result_token_revoked_at=existing.result_token_revoked_at,
                result_email_status=existing.result_email_status,
                result_email_provider_message_id=existing.result_email_provider_message_id,
                result_email_sent_at=existing.result_email_sent_at,
                result_email_last_error=existing.result_email_last_error,
                result_email_attempt_count=existing.result_email_attempt_count,
                delivered_at=existing.delivered_at,
                last_download_at=existing.last_download_at,
                download_count=existing.download_count,
                review_commentary=existing.review_commentary,
                review_actions=existing.review_actions,
                review_started_at=existing.review_started_at,
                review_updated_at=existing.review_updated_at,
                approved_at=existing.approved_at,
                released_at=existing.released_at,
                review_operator_id=existing.review_operator_id,
                final_pdf_object_path=existing.final_pdf_object_path,
                final_pdf_size_bytes=existing.final_pdf_size_bytes,
                final_excel_object_path=existing.final_excel_object_path,
                final_excel_size_bytes=existing.final_excel_size_bytes,
            )
        self.orders[order.order_id] = order
        self.checkout_index[order.stripe_checkout_session_id] = order.order_id
        return order

    def get_order_by_checkout_session(self, checkout_session_id: str) -> Order | None:
        order_id = self.checkout_index.get(checkout_session_id)
        if not order_id:
            return None
        return self.orders.get(order_id)

    def get_order(self, order_id: str) -> Order | None:
        return self.orders.get(order_id)

    def list_orders(self) -> list[Order]:
        return list(self.orders.values())

    def get_order_by_token_hash(self, token_hash: str) -> Order | None:
        for order in self.orders.values():
            if order.token_hash == token_hash:
                return order
        return None

    def get_order_by_result_token_hash(self, token_hash: str) -> Order | None:
        for order in self.orders.values():
            if order.result_token_hash == token_hash:
                return order
        return None

    def save_order(self, order: Order) -> Order:
        self.orders[order.order_id] = order
        self.checkout_index[order.stripe_checkout_session_id] = order.order_id
        return order

    def claim_email_send(self, order_id: str, claimed_at: datetime) -> Order | None:
        order = self.orders.get(order_id)
        if not order:
            return None
        if order.email_status in {"SENT", "SENDING"}:
            return None
        claimed = replace(
            order,
            email_status="SENDING",
            email_attempt_count=order.email_attempt_count + 1,
            email_last_error=None,
            updated_at=claimed_at,
        )
        self.orders[order_id] = claimed
        return claimed

    def claim_result_email_send(self, order_id: str, claimed_at: datetime) -> Order | None:
        order = self.orders.get(order_id)
        if not order:
            return None
        if order.result_email_status in {"SENT", "SENDING"}:
            return None
        if order.result_status != "READY":
            return None
        if order.product_code == "FULL_ANALYSIS":
            pass
        elif order.product_code == "EXPERT_REVIEW" and order.expert_review_status == "RELEASED":
            pass
        else:
            return None
        claimed = replace(
            order,
            result_email_status="SENDING",
            result_email_attempt_count=order.result_email_attempt_count + 1,
            result_email_last_error=None,
            updated_at=claimed_at,
        )
        self.orders[order_id] = claimed
        return claimed

    def claim_expert_review_release(self, order_id: str, released_at: datetime) -> Order | None:
        order = self.orders.get(order_id)
        if not order:
            return None
        if order.product_code != "EXPERT_REVIEW":
            return None
        if order.expert_review_status == "RELEASED":
            return None
        if order.expert_review_status not in {"IN_REVIEW", "APPROVED"}:
            return None
        claimed = replace(order, expert_review_status="APPROVED", approved_at=order.approved_at or released_at, updated_at=released_at)
        self.orders[order_id] = claimed
        return claimed

    def claim_analysis_processing(self, order_id: str, claimed_at: datetime, retry_failed: bool = False) -> Order | None:
        order = self.orders.get(order_id)
        if not order:
            return None
        if order.fulfilment_status == "READY" or order.analysis_status == "COMPLETED":
            return None
        if order.fulfilment_status == "PROCESSING" or order.analysis_status == "PROCESSING":
            return None
        if order.fulfilment_status == "PROCESSING_FAILED" and not retry_failed:
            return None
        if order.fulfilment_status not in {"VALIDATED", "PROCESSING_FAILED"}:
            return None
        claimed = replace(
            order,
            fulfilment_status="PROCESSING",
            analysis_status="PROCESSING",
            analysis_started_at=claimed_at,
            analysis_completed_at=None,
            analysis_error_code=None,
            result_status="NOT_READY",
            updated_at=claimed_at,
        )
        self.orders[order_id] = claimed
        return claimed

    def save_customer_session(self, session: CustomerSession) -> CustomerSession:
        self.customer_sessions[session.session_hash] = session
        return session

    def get_customer_session(self, session_hash: str) -> CustomerSession | None:
        return self.customer_sessions.get(session_hash)

    def delete_customer_session(self, session_hash: str) -> None:
        self.customer_sessions.pop(session_hash, None)


def make_order_id(checkout_session_id: str) -> str:
    digest = hashlib.sha256(checkout_session_id.encode("utf-8")).hexdigest()[:24]
    return f"ord_{digest}"


def process_stripe_event(
    event: dict[str, Any],
    store: OrderStore,
    settings: Settings,
    fulfilment_service: Any | None = None,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    received_at = received_at or utc_now()
    event_id = str(event.get("id") or "")
    event_type = str(event.get("type") or "")
    if not event_id:
        raise FulfilmentError("Stripe event is missing an id")

    reservation = store.reserve_event(event_id, event_type, received_at)
    if reservation == "duplicate_success":
        return {"status": "duplicate_ignored", "event_id": event_id}
    if reservation == "duplicate_processing":
        return {"status": "duplicate_in_progress", "event_id": event_id}

    try:
        if event_type not in {
            "checkout.session.completed",
            "checkout.session.async_payment_succeeded",
            "checkout.session.async_payment_failed",
        }:
            store.mark_event_processed(event_id, "", utc_now())
            return {"status": "ignored", "event_id": event_id, "event_type": event_type}

        session = event.get("data", {}).get("object")
        if not isinstance(session, dict):
            raise MissingCheckoutSessionError("Stripe event does not contain a Checkout Session")

        order = build_order_from_session(event_type, session, settings)
        order = store.upsert_order(order)
        if fulfilment_service and order.payment_status == "PAID":
            order = fulfilment_service.fulfil_paid_order(order)
        store.mark_event_processed(event_id, order.order_id, utc_now())
        return {
            "status": "processed",
            "event_id": event_id,
            "order_id": order.order_id,
            "payment_status": order.payment_status,
            "product_code": order.product_code,
            "fulfilment_status": order.fulfilment_status,
            "email_status": order.email_status,
        }
    except FulfilmentError as exc:
        store.mark_event_failed(event_id, exc.error_code, utc_now())
        raise
    except Exception:
        store.mark_event_failed(event_id, "UNEXPECTED_ERROR", utc_now())
        raise


def build_order_from_session(event_type: str, session: dict[str, Any], settings: Settings) -> Order:
    checkout_session_id = str(session.get("id") or "")
    if not checkout_session_id:
        raise MissingCheckoutSessionError("Checkout Session is missing an id")

    price_id, product_id = extract_price_and_product(session)
    product = identify_product(price_id, product_id, session.get("metadata") or {}, settings)
    validate_price_product_consistency(product, price_id, product_id)
    validate_amount(product, session)

    payment_status = payment_status_for_event(event_type, session)
    customer_details = session.get("customer_details") or {}
    customer_name = customer_details.get("name") or session.get("customer_name")
    customer_email = customer_details.get("email") or session.get("customer_email")
    customer_flags = []
    if not customer_name:
        customer_flags.append("MISSING_CUSTOMER_NAME")
    if not customer_email:
        customer_flags.append("MISSING_CUSTOMER_EMAIL")

    now = utc_now()
    return Order(
        order_id=make_order_id(checkout_session_id),
        stripe_checkout_session_id=checkout_session_id,
        stripe_payment_intent_id=session.get("payment_intent"),
        stripe_customer_id=session.get("customer"),
        stripe_price_id=price_id,
        stripe_product_id=product_id,
        product_code=product.code,
        amount_total=session.get("amount_total"),
        currency=(session.get("currency") or "").lower() or None,
        customer_name=customer_name,
        customer_email=customer_email,
        payment_status=payment_status,
        fulfilment_status="NOT_STARTED",
        created_at=now,
        updated_at=now,
        paid_at=now if payment_status == "PAID" else None,
        customer_data_flags=customer_flags,
    )


def payment_status_for_event(event_type: str, session: dict[str, Any]) -> str:
    if event_type == "checkout.session.async_payment_failed":
        return "FAILED"
    if event_type == "checkout.session.async_payment_succeeded":
        return "PAID"
    if session.get("payment_status") == "paid":
        return "PAID"
    if session.get("payment_status") in {"unpaid", "no_payment_required"}:
        return "PENDING"
    return "PENDING"


def extract_price_and_product(session: dict[str, Any]) -> tuple[str | None, str | None]:
    line_items = session.get("line_items") or {}
    data = line_items.get("data") or []
    if data:
        price = data[0].get("price") or {}
        product = price.get("product")
        product_id = product.get("id") if isinstance(product, dict) else product
        return price.get("id"), product_id

    metadata = session.get("metadata") or {}
    return metadata.get("stripe_price_id") or metadata.get("price_id"), metadata.get(
        "stripe_product_id"
    ) or metadata.get("product_id")


def identify_product(
    price_id: str | None,
    product_id: str | None,
    metadata: dict[str, Any],
    settings: Settings,
) -> ProductConfig:
    configured_products = settings.products
    metadata_code = metadata.get("senalo_product_code") or metadata.get("product_code")
    if metadata_code in configured_products:
        return configured_products[metadata_code]
    if metadata_code:
        raise ProductMappingError("Unknown SENALO product metadata code")

    raise ProductMappingError("Missing required SENALO product metadata")


def validate_price_product_consistency(
    product: ProductConfig,
    price_id: str | None,
    product_id: str | None,
) -> None:
    if product.price_id and price_id and price_id != product.price_id:
        raise ProductConflictError("Stripe Price ID conflicts with SENALO product metadata")
    if product.product_id and product_id and product_id != product.product_id:
        raise ProductConflictError("Stripe Product ID conflicts with SENALO product metadata")


def validate_amount(product: ProductConfig, session: dict[str, Any]) -> None:
    amount = session.get("amount_total")
    currency = (session.get("currency") or "").lower()
    if currency and currency != product.expected_currency:
        raise AmountMismatchError("Checkout Session currency does not match configured product")
    if amount is not None and int(amount) != product.expected_amount:
        raise AmountMismatchError("Checkout Session amount does not match configured product")
