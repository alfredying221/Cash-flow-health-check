from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .models import Order


SUPPORT_EMAIL = "hello@senalo.com.au"
FOOTER = "SENALO\nSee clearly. Decide better.\nhttps://senalo.com.au"


@dataclass(frozen=True)
class EmailMessage:
    to_email: str
    subject: str
    body: str
    reply_to: str | None = None


@dataclass(frozen=True)
class EmailSendResult:
    provider_message_id: str


class EmailProvider(Protocol):
    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        ...


class EmailDeliveryError(Exception):
    pass


class MissingCustomerEmailError(EmailDeliveryError):
    pass


class ResendEmailProvider:
    def __init__(self, api_key: str, from_email: str, reply_to: str | None = None) -> None:
        self.api_key = api_key
        self.from_email = from_email
        self.reply_to = reply_to

    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        try:
            import resend
        except ImportError as exc:
            raise EmailDeliveryError("RESEND_PACKAGE_MISSING") from exc

        resend.api_key = self.api_key
        payload = {
            "from": self.from_email,
            "to": [message.to_email],
            "subject": message.subject,
            "text": message.body,
            "headers": {"Idempotency-Key": idempotency_key},
        }
        reply_to = message.reply_to or self.reply_to
        if reply_to:
            payload["reply_to"] = reply_to

        try:
            response = resend.Emails.send(payload)
        except Exception as exc:
            raise EmailDeliveryError("RESEND_SEND_FAILED") from exc

        message_id = response.get("id") if isinstance(response, dict) else getattr(response, "id", None)
        if not message_id:
            raise EmailDeliveryError("RESEND_MISSING_MESSAGE_ID")
        return EmailSendResult(provider_message_id=str(message_id))


class UnconfiguredEmailProvider:
    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        raise EmailDeliveryError("EMAIL_PROVIDER_NOT_CONFIGURED")


class RecordingEmailProvider:
    def __init__(self, fail_with: str | None = None) -> None:
        self.fail_with = fail_with
        self.sent: list[tuple[EmailMessage, str]] = []

    def send(self, message: EmailMessage, *, idempotency_key: str) -> EmailSendResult:
        if self.fail_with:
            raise EmailDeliveryError(self.fail_with)
        self.sent.append((message, idempotency_key))
        return EmailSendResult(provider_message_id=f"msg_{len(self.sent)}")


def customer_greeting(order: Order) -> str:
    if order.customer_name:
        return f"Hi {order.customer_name},"
    return "Hi,"


def build_order_email(order: Order, secure_url: str, reply_to: str | None = None) -> EmailMessage:
    if not order.customer_email:
        raise MissingCustomerEmailError("MISSING_CUSTOMER_EMAIL")
    if order.product_code == "FULL_ANALYSIS":
        subject = "SENALO Full Analysis – Payment Received / Next Steps"
        body = full_analysis_body(order, secure_url)
    elif order.product_code == "EXPERT_REVIEW":
        subject = "SENALO Expert Review – Payment Received / Next Steps"
        body = expert_review_body(order, secure_url)
    else:
        raise EmailDeliveryError("UNKNOWN_PRODUCT_CODE")
    return EmailMessage(
        to_email=order.customer_email,
        subject=subject,
        body=body,
        reply_to=reply_to,
    )


def build_result_ready_email(order: Order, secure_url: str, reply_to: str | None = None) -> EmailMessage:
    if not order.customer_email:
        raise MissingCustomerEmailError("MISSING_CUSTOMER_EMAIL")
    if order.product_code == "FULL_ANALYSIS":
        subject = "SENALO Full Analysis – Your Analysis Is Ready"
        body = "\n\n".join(
            [
                customer_greeting(order),
                "Your SENALO Full Analysis is ready.",
                "You can securely access your PDF report and Excel analysis using the link below:",
                secure_url,
                "For security, this link is time-limited.",
                f"If you need assistance, please contact {SUPPORT_EMAIL}.",
                FOOTER,
            ]
        )
    elif order.product_code == "EXPERT_REVIEW":
        subject = "SENALO Expert Review – Your Review Is Ready"
        body = "\n\n".join(
            [
                customer_greeting(order),
                "Your SENALO Expert Review is ready.",
                "You can securely access your final human-reviewed PDF report and Excel analysis using the link below:",
                secure_url,
                "For security, this link is time-limited.",
                f"If you need assistance, please contact {SUPPORT_EMAIL}.",
                FOOTER,
            ]
        )
    else:
        raise EmailDeliveryError("RESULT_EMAIL_NOT_ALLOWED")
    return EmailMessage(
        to_email=order.customer_email,
        subject=subject,
        body=body,
        reply_to=reply_to,
    )


def full_analysis_body(order: Order, secure_url: str) -> str:
    return "\n\n".join(
        [
            customer_greeting(order),
            "Thank you. Your payment for SENALO Full Analysis has been received.",
            "The next step is to provide your financial data using this secure link:",
            secure_url,
            "Your Full Analysis includes a 12-month forecast, Base / Downside / Upside scenarios, a management summary, a PDF report, and an Excel analysis.",
            f"If you need assistance, contact {SUPPORT_EMAIL}.",
            FOOTER,
        ]
    )


def expert_review_body(order: Order, secure_url: str) -> str:
    return "\n\n".join(
        [
            customer_greeting(order),
            "Thank you. Your payment for SENALO Expert Review has been received.",
            "The next step is to provide your financial data using this secure link:",
            secure_url,
            "Your Expert Review includes the Full Analysis, manual review of your financial data and assumptions, customised commentary, and 3–5 prioritised management actions.",
            "After your data is submitted, SENALO will review it and contact you with the next steps.",
            f"If you need assistance, contact {SUPPORT_EMAIL}.",
            FOOTER,
        ]
    )
