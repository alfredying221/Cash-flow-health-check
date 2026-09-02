from __future__ import annotations

import logging
from dataclasses import replace

from .config import Settings
from .email_service import (
    EmailDeliveryError,
    EmailProvider,
    MissingCustomerEmailError,
    build_order_email,
)
from .models import Order, utc_now
from .orders import OrderStore
from .tokens import (
    TokenConfigurationError,
    TokenValidationError,
    attach_new_token,
    build_secure_upload_url,
    reproduce_token,
)


logger = logging.getLogger("senalo.fulfilment")


class OrderFulfilmentService:
    def __init__(self, store: OrderStore, settings: Settings, email_provider: EmailProvider) -> None:
        self.store = store
        self.settings = settings
        self.email_provider = email_provider

    def fulfil_paid_order(self, order: Order) -> Order:
        if order.payment_status != "PAID":
            return order
        if order.product_code not in {"FULL_ANALYSIS", "EXPERT_REVIEW"}:
            return order
        if order.email_status == "SENT":
            return order

        with_token = order
        raw_token = None
        try:
            if not with_token.token_hash or not with_token.token_seed or with_token.token_revoked_at:
                with_token, raw_token = attach_new_token(
                    with_token,
                    derivation_secret=self.settings.token_derivation_secret,
                    expiry_days=self.settings.token_expiry_days,
                )
            else:
                raw_token = reproduce_token(with_token, self.settings.token_derivation_secret)
        except (TokenConfigurationError, TokenValidationError) as exc:
            failed = replace(
                with_token,
                email_status="FAILED",
                email_last_error=str(exc) or exc.__class__.__name__,
                updated_at=utc_now(),
            )
            return self.store.save_order(failed)

        if not order.token_hash or not order.token_seed or order.token_revoked_at:
            self.store.save_order(with_token)

        claimed = self.store.claim_email_send(with_token.order_id, utc_now())
        if not claimed:
            return self.store.get_order(with_token.order_id) or with_token

        secure_url = build_secure_upload_url(
            self.settings.senalo_public_fulfilment_base_url,
            raw_token,
        )
        try:
            email = build_order_email(claimed, secure_url, self.settings.senalo_email_reply_to)
            result = self.email_provider.send(
                email,
                idempotency_key=f"payment-confirmation/{claimed.order_id}",
            )
        except MissingCustomerEmailError:
            failed = replace(
                claimed,
                email_status="FAILED",
                email_last_error="MISSING_CUSTOMER_EMAIL",
                updated_at=utc_now(),
            )
            logger.warning("order_email_failed", extra={"order_id": claimed.order_id, "error_code": "MISSING_CUSTOMER_EMAIL"})
            return self.store.save_order(failed)
        except EmailDeliveryError as exc:
            failed = replace(
                claimed,
                email_status="FAILED",
                email_last_error=str(exc) or exc.__class__.__name__,
                updated_at=utc_now(),
            )
            logger.warning("order_email_failed", extra={"order_id": claimed.order_id, "error_code": failed.email_last_error})
            return self.store.save_order(failed)

        sent = replace(
            claimed,
            email_status="SENT",
            email_provider_message_id=result.provider_message_id,
            email_sent_at=utc_now(),
            email_last_error=None,
            fulfilment_status="AWAITING_UPLOAD",
            updated_at=utc_now(),
        )
        logger.info("order_email_sent", extra={"order_id": claimed.order_id})
        return self.store.save_order(sent)

    def retry_failed_email(self, order_id: str) -> Order:
        order = self.store.get_order(order_id)
        if not order:
            raise EmailDeliveryError("ORDER_NOT_FOUND")
        if order.email_status == "SENT":
            return order
        if order.email_status not in {"FAILED", "NOT_SENT"}:
            raise EmailDeliveryError("EMAIL_NOT_RETRYABLE")

        try:
            if not order.token_hash or not order.token_seed or order.token_revoked_at:
                with_token, raw_token = attach_new_token(
                    order,
                    derivation_secret=self.settings.token_derivation_secret,
                    expiry_days=self.settings.token_expiry_days,
                )
                self.store.save_order(with_token)
            else:
                with_token = order
                raw_token = reproduce_token(order, self.settings.token_derivation_secret)
        except (TokenConfigurationError, TokenValidationError) as exc:
            failed = replace(
                order,
                email_status="FAILED",
                email_last_error=str(exc) or exc.__class__.__name__,
                updated_at=utc_now(),
            )
            return self.store.save_order(failed)

        claimed = self.store.claim_email_send(order_id, utc_now())
        if not claimed:
            return self.store.get_order(order_id) or with_token

        secure_url = build_secure_upload_url(
            self.settings.senalo_public_fulfilment_base_url,
            raw_token,
        )
        try:
            email = build_order_email(claimed, secure_url, self.settings.senalo_email_reply_to)
            result = self.email_provider.send(
                email,
                idempotency_key=f"payment-confirmation/{claimed.order_id}",
            )
        except MissingCustomerEmailError:
            failed = replace(
                claimed,
                email_status="FAILED",
                email_last_error="MISSING_CUSTOMER_EMAIL",
                updated_at=utc_now(),
            )
            return self.store.save_order(failed)
        except EmailDeliveryError as exc:
            failed = replace(
                claimed,
                email_status="FAILED",
                email_last_error=str(exc) or exc.__class__.__name__,
                updated_at=utc_now(),
            )
            return self.store.save_order(failed)

        sent = replace(
            claimed,
            email_status="SENT",
            email_provider_message_id=result.provider_message_id,
            email_sent_at=utc_now(),
            email_last_error=None,
            fulfilment_status="AWAITING_UPLOAD",
            updated_at=utc_now(),
        )
        return self.store.save_order(sent)
