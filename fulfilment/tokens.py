from __future__ import annotations

import hashlib
import hmac
import base64
import secrets
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from .models import Order, utc_now
from .orders import OrderStore


DEFAULT_TOKEN_EXPIRY_DAYS = 14


class TokenValidationError(Exception):
    pass


class TokenConfigurationError(Exception):
    pass


def generate_token_seed() -> str:
    return secrets.token_urlsafe(32)


def require_derivation_secret(secret: str | None) -> str:
    if not secret or len(secret.encode("utf-8")) < 32:
        raise TokenConfigurationError("TOKEN_DERIVATION_SECRET_INVALID")
    return secret


def derive_raw_token(token_seed: str, token_version: int, derivation_secret: str | None) -> str:
    secret = require_derivation_secret(derivation_secret)
    message = f"senalo-upload-token:v{token_version}:{token_seed}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def token_expiry(now: datetime | None = None, days: int = DEFAULT_TOKEN_EXPIRY_DAYS) -> datetime:
    base = now or utc_now()
    return base + timedelta(days=days)


def is_malformed_token(raw_token: str | None) -> bool:
    if not raw_token or len(raw_token) < 32 or len(raw_token) > 256:
        return True
    return any(ch.isspace() for ch in raw_token)


def attach_new_token(
    order: Order,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
    expiry_days: int = DEFAULT_TOKEN_EXPIRY_DAYS,
) -> tuple[Order, str]:
    issued_at = now or utc_now()
    token_seed = generate_token_seed()
    token_version = (order.token_version or 0) + 1 if order.token_seed else 1
    raw_token = derive_raw_token(token_seed, token_version, derivation_secret)
    updated = replace(
        order,
        token_seed=token_seed,
        token_version=token_version,
        token_hash=hash_token(raw_token),
        token_created_at=issued_at,
        token_expires_at=token_expiry(issued_at, expiry_days),
        token_revoked_at=None,
        updated_at=issued_at,
    )
    return updated, raw_token


def reproduce_token(order: Order, derivation_secret: str | None) -> str:
    if not order.token_seed:
        raise TokenValidationError("Token cannot be reproduced")
    return derive_raw_token(order.token_seed, order.token_version, derivation_secret)


def reissue_token(
    order_id: str,
    store: OrderStore,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
    expiry_days: int = DEFAULT_TOKEN_EXPIRY_DAYS,
) -> tuple[Order, str]:
    order = store.get_order(order_id)
    if not order:
        raise TokenValidationError("Token cannot be reissued")
    revoked = replace(order, token_revoked_at=now or utc_now())
    updated, raw_token = attach_new_token(
        revoked,
        derivation_secret=derivation_secret,
        now=now,
        expiry_days=expiry_days,
    )
    return store.save_order(updated), raw_token


def validate_token(
    raw_token: str | None,
    store: OrderStore,
    *,
    derivation_secret: str | None,
    now: datetime | None = None,
) -> Order:
    require_derivation_secret(derivation_secret)
    if is_malformed_token(raw_token):
        raise TokenValidationError("Invalid access token")
    token_hash = hash_token(raw_token or "")
    order = store.get_order_by_token_hash(token_hash)
    if not order or not order.token_hash or not hmac.compare_digest(order.token_hash, token_hash):
        raise TokenValidationError("Invalid access token")

    current_time = now or utc_now()
    if order.token_revoked_at is not None:
        raise TokenValidationError("Invalid access token")
    expires_at = order.token_expires_at
    if expires_at is None:
        raise TokenValidationError("Invalid access token")
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= current_time:
        raise TokenValidationError("Invalid access token")
    return order


def build_secure_upload_url(base_url: str, raw_token: str) -> str:
    clean_base = base_url.rstrip("/")
    return f"{clean_base}/upload?t={raw_token}"
