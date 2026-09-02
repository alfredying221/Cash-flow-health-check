from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import CustomerSession, CustomerSessionPurpose, Order, utc_now
from .orders import OrderStore
from .result_delivery import ResultTokenExpiredError, validate_result_token
from .tokens import TokenConfigurationError, TokenValidationError, hash_token, validate_token
from .upload_intake import UploadNotAllowedError, ensure_upload_allowed


CUSTOMER_SESSION_COOKIE = "senalo_customer_session"
CUSTOMER_SESSION_MINUTES = 45


class CustomerSessionError(Exception):
    pass


class CustomerSessionExpiredError(CustomerSessionError):
    pass


@dataclass(frozen=True)
class SessionExchangeResult:
    raw_session_id: str
    session: CustomerSession
    next_path: str


def generate_session_id() -> str:
    return secrets.token_urlsafe(48)


def session_expiry(now: datetime | None = None, minutes: int = CUSTOMER_SESSION_MINUTES) -> datetime:
    return (now or utc_now()) + timedelta(minutes=minutes)


def exchange_customer_token(
    raw_token: str | None,
    store: OrderStore,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
    session_minutes: int = CUSTOMER_SESSION_MINUTES,
) -> SessionExchangeResult:
    current_time = now or utc_now()
    try:
        order = validate_token(raw_token, store, derivation_secret=derivation_secret, now=current_time)
        ensure_upload_allowed(order)
        return create_customer_session(order, "upload", store, now=current_time, session_minutes=session_minutes)
    except (TokenConfigurationError, TokenValidationError, UploadNotAllowedError):
        pass

    try:
        order = validate_result_token(raw_token, store, derivation_secret=derivation_secret, now=current_time)
        return create_customer_session(order, "result", store, now=current_time, session_minutes=session_minutes)
    except (TokenConfigurationError, TokenValidationError, ResultTokenExpiredError) as exc:
        raise CustomerSessionError("Invalid customer access token") from exc


def create_customer_session(
    order: Order,
    purpose: CustomerSessionPurpose,
    store: OrderStore,
    *,
    now: datetime | None = None,
    session_minutes: int = CUSTOMER_SESSION_MINUTES,
) -> SessionExchangeResult:
    current_time = now or utc_now()
    raw_session_id = generate_session_id()
    session = CustomerSession(
        session_hash=hash_token(raw_session_id),
        order_id=order.order_id,
        purpose=purpose,
        created_at=current_time,
        expires_at=session_expiry(current_time, session_minutes),
    )
    store.save_customer_session(session)
    next_path = "/upload" if purpose == "upload" else "/result"
    return SessionExchangeResult(raw_session_id=raw_session_id, session=session, next_path=next_path)


def validate_customer_session(
    raw_session_id: str | None,
    purpose: CustomerSessionPurpose,
    store: OrderStore,
    *,
    now: datetime | None = None,
) -> tuple[CustomerSession, Order]:
    if not raw_session_id or len(raw_session_id) < 32 or len(raw_session_id) > 256 or any(ch.isspace() for ch in raw_session_id):
        raise CustomerSessionError("Invalid customer session")
    session_hash = hash_token(raw_session_id)
    session = store.get_customer_session(session_hash)
    if not session or session.revoked_at is not None or session.purpose != purpose:
        raise CustomerSessionError("Invalid customer session")
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= (now or utc_now()):
        raise CustomerSessionExpiredError("Customer session expired")
    order = store.get_order(session.order_id)
    if not order:
        raise CustomerSessionError("Invalid customer session")
    return session, order
