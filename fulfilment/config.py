from __future__ import annotations

import os
from dataclasses import dataclass


FULL_ANALYSIS_AMOUNT_AUD_CENTS = 3900
EXPERT_REVIEW_AMOUNT_AUD_CENTS = 14900


@dataclass(frozen=True)
class ProductConfig:
    code: str
    name: str
    price_id: str | None
    product_id: str | None
    expected_amount: int
    expected_currency: str = "aud"


@dataclass(frozen=True)
class Settings:
    stripe_webhook_secret: str
    stripe_secret_key: str | None
    google_cloud_project: str | None
    full_analysis_price_id: str | None
    expert_review_price_id: str | None
    full_analysis_product_id: str | None
    expert_review_product_id: str | None
    resend_api_key: str | None = None
    senalo_email_from: str | None = None
    senalo_email_reply_to: str | None = None
    senalo_public_fulfilment_base_url: str = "http://127.0.0.1:8080"
    token_expiry_days: int = 14
    result_token_expiry_days: int = 30
    token_derivation_secret: str | None = None
    upload_bucket: str | None = None
    max_upload_bytes: int = 5 * 1024 * 1024
    operator_auth_token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            stripe_webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            stripe_secret_key=os.getenv("STRIPE_SECRET_KEY"),
            google_cloud_project=os.getenv("GOOGLE_CLOUD_PROJECT"),
            full_analysis_price_id=os.getenv("SENALO_FULL_ANALYSIS_PRICE_ID"),
            expert_review_price_id=os.getenv("SENALO_EXPERT_REVIEW_PRICE_ID"),
            full_analysis_product_id=os.getenv("SENALO_FULL_ANALYSIS_PRODUCT_ID"),
            expert_review_product_id=os.getenv("SENALO_EXPERT_REVIEW_PRODUCT_ID"),
            resend_api_key=os.getenv("RESEND_API_KEY"),
            senalo_email_from=os.getenv("SENALO_EMAIL_FROM"),
            senalo_email_reply_to=os.getenv("SENALO_EMAIL_REPLY_TO", "hello@senalo.com.au"),
            senalo_public_fulfilment_base_url=os.getenv(
                "SENALO_PUBLIC_FULFILMENT_BASE_URL", "http://127.0.0.1:8080"
            ),
            token_expiry_days=int(os.getenv("SENALO_TOKEN_EXPIRY_DAYS", "14")),
            result_token_expiry_days=int(os.getenv("SENALO_RESULT_TOKEN_EXPIRY_DAYS", "30")),
            token_derivation_secret=os.getenv("SENALO_TOKEN_DERIVATION_SECRET"),
            upload_bucket=os.getenv("SENALO_UPLOAD_BUCKET"),
            max_upload_bytes=int(os.getenv("SENALO_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))),
            operator_auth_token=os.getenv("SENALO_OPERATOR_AUTH_TOKEN"),
        )

    @property
    def products(self) -> dict[str, ProductConfig]:
        return {
            "FULL_ANALYSIS": ProductConfig(
                code="FULL_ANALYSIS",
                name="SENALO Full Analysis",
                price_id=self.full_analysis_price_id,
                product_id=self.full_analysis_product_id,
                expected_amount=FULL_ANALYSIS_AMOUNT_AUD_CENTS,
            ),
            "EXPERT_REVIEW": ProductConfig(
                code="EXPERT_REVIEW",
                name="SENALO Expert Review",
                price_id=self.expert_review_price_id,
                product_id=self.expert_review_product_id,
                expected_amount=EXPERT_REVIEW_AMOUNT_AUD_CENTS,
            ),
        }
