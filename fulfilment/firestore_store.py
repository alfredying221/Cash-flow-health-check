from __future__ import annotations

from datetime import datetime

from .models import CustomerSession, Order, StripeEventRecord
from .orders import OrderStore


class FirestoreOrderStore(OrderStore):
    def __init__(self, project: str | None = None):
        from google.cloud import firestore

        self.client = firestore.Client(project=project)

    def reserve_event(self, event_id: str, event_type: str, received_at: datetime) -> str:
        from google.cloud import firestore

        event_ref = self.client.collection("stripe_events").document(event_id)

        def reserve(transaction):
            snapshot = event_ref.get(transaction=transaction)
            if snapshot.exists:
                status = snapshot.to_dict().get("processing_status")
                if status == "PROCESSED":
                    return "duplicate_success"
                if status == "PROCESSING":
                    return "duplicate_processing"
            transaction.set(
                event_ref,
                StripeEventRecord(
                    event_id=event_id,
                    event_type=event_type,
                    received_at=received_at,
                    processing_status="PROCESSING",
                ).to_dict(),
            )
            return "reserved"

        transaction = self.client.transaction()
        return firestore.transactional(reserve)(transaction)

    def mark_event_processed(self, event_id: str, order_id: str, processed_at: datetime) -> None:
        self.client.collection("stripe_events").document(event_id).update(
            {
                "processing_status": "PROCESSED",
                "processed_at": processed_at,
                "order_id": order_id,
                "error_code": None,
            }
        )

    def mark_event_failed(self, event_id: str, error_code: str, processed_at: datetime) -> None:
        self.client.collection("stripe_events").document(event_id).update(
            {
                "processing_status": "FAILED",
                "processed_at": processed_at,
                "error_code": error_code,
            }
        )

    def upsert_order(self, order: Order) -> Order:
        order_ref = self.client.collection("orders").document(order.order_id)
        existing = order_ref.get()
        data = order.to_dict()
        if existing.exists:
            existing_data = existing.to_dict()
            data["created_at"] = existing_data.get("created_at", data["created_at"])
            for field in [
                "token_hash",
                "token_seed",
                "token_version",
                "token_created_at",
                "token_expires_at",
                "token_revoked_at",
                "email_status",
                "email_provider_message_id",
                "email_sent_at",
                "email_last_error",
                "email_attempt_count",
                "fulfilment_status",
                "business_type",
                "opening_cash",
                "upload_status",
                "upload_object_path",
                "upload_original_filename",
                "upload_content_type",
                "upload_size_bytes",
                "upload_created_at",
                "validation_status",
                "validation_error_code",
                "validated_at",
                "analysis_status",
                "analysis_started_at",
                "analysis_completed_at",
                "analysis_error_code",
                "pdf_object_path",
                "pdf_size_bytes",
                "excel_object_path",
                "excel_size_bytes",
                "result_status",
                "expert_review_status",
                "result_token_hash",
                "result_token_seed",
                "result_token_version",
                "result_token_created_at",
                "result_token_expires_at",
                "result_token_revoked_at",
                "result_email_status",
                "result_email_provider_message_id",
                "result_email_sent_at",
                "result_email_last_error",
                "result_email_attempt_count",
                "delivered_at",
                "last_download_at",
                "download_count",
                "review_commentary",
                "review_actions",
                "review_started_at",
                "review_updated_at",
                "approved_at",
                "released_at",
                "review_operator_id",
                "final_pdf_object_path",
                "final_pdf_size_bytes",
                "final_excel_object_path",
                "final_excel_size_bytes",
            ]:
                if field in existing_data:
                    data[field] = existing_data[field]
        order_ref.set(data, merge=True)
        return Order(**data)

    def get_order_by_checkout_session(self, checkout_session_id: str) -> Order | None:
        query = (
            self.client.collection("orders")
            .where("stripe_checkout_session_id", "==", checkout_session_id)
            .limit(1)
        )
        for snapshot in query.stream():
            data = snapshot.to_dict()
            return Order(**data)
        return None

    def get_order(self, order_id: str) -> Order | None:
        snapshot = self.client.collection("orders").document(order_id).get()
        if not snapshot.exists:
            return None
        return Order(**snapshot.to_dict())

    def list_orders(self) -> list[Order]:
        return [Order(**snapshot.to_dict()) for snapshot in self.client.collection("orders").stream()]

    def get_order_by_token_hash(self, token_hash: str) -> Order | None:
        query = self.client.collection("orders").where("token_hash", "==", token_hash).limit(1)
        for snapshot in query.stream():
            return Order(**snapshot.to_dict())
        return None

    def get_order_by_result_token_hash(self, token_hash: str) -> Order | None:
        query = self.client.collection("orders").where("result_token_hash", "==", token_hash).limit(1)
        for snapshot in query.stream():
            return Order(**snapshot.to_dict())
        return None

    def save_order(self, order: Order) -> Order:
        self.client.collection("orders").document(order.order_id).set(order.to_dict(), merge=True)
        return order

    def claim_email_send(self, order_id: str, claimed_at: datetime) -> Order | None:
        from google.cloud import firestore

        order_ref = self.client.collection("orders").document(order_id)

        def claim(transaction):
            snapshot = order_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            if data.get("email_status") in {"SENT", "SENDING"}:
                return None
            data.update(
                {
                    "email_status": "SENDING",
                    "email_attempt_count": int(data.get("email_attempt_count") or 0) + 1,
                    "email_last_error": None,
                    "updated_at": claimed_at,
                }
            )
            transaction.set(order_ref, data, merge=True)
            return Order(**data)

        transaction = self.client.transaction()
        return firestore.transactional(claim)(transaction)

    def claim_expert_review_release(self, order_id: str, released_at: datetime) -> Order | None:
        from google.cloud import firestore

        order_ref = self.client.collection("orders").document(order_id)

        def claim(transaction):
            snapshot = order_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            if data.get("product_code") != "EXPERT_REVIEW":
                return None
            if data.get("expert_review_status") == "RELEASED":
                return None
            if data.get("expert_review_status") not in {"IN_REVIEW", "APPROVED"}:
                return None
            data.update(
                {
                    "expert_review_status": "APPROVED",
                    "approved_at": data.get("approved_at") or released_at,
                    "updated_at": released_at,
                }
            )
            transaction.set(order_ref, data, merge=True)
            return Order(**data)

        transaction = self.client.transaction()
        return firestore.transactional(claim)(transaction)

    def claim_result_email_send(self, order_id: str, claimed_at: datetime) -> Order | None:
        from google.cloud import firestore

        order_ref = self.client.collection("orders").document(order_id)

        def claim(transaction):
            snapshot = order_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            if data.get("result_email_status") in {"SENT", "SENDING"}:
                return None
            if data.get("result_status") != "READY":
                return None
            if data.get("product_code") == "FULL_ANALYSIS":
                pass
            elif data.get("product_code") == "EXPERT_REVIEW" and data.get("expert_review_status") == "RELEASED":
                pass
            else:
                return None
            data.update(
                {
                    "result_email_status": "SENDING",
                    "result_email_attempt_count": int(data.get("result_email_attempt_count") or 0) + 1,
                    "result_email_last_error": None,
                    "updated_at": claimed_at,
                }
            )
            transaction.set(order_ref, data, merge=True)
            return Order(**data)

        transaction = self.client.transaction()
        return firestore.transactional(claim)(transaction)

    def claim_analysis_processing(self, order_id: str, claimed_at: datetime, retry_failed: bool = False) -> Order | None:
        from google.cloud import firestore

        order_ref = self.client.collection("orders").document(order_id)

        def claim(transaction):
            snapshot = order_ref.get(transaction=transaction)
            if not snapshot.exists:
                return None
            data = snapshot.to_dict()
            fulfilment_status = data.get("fulfilment_status")
            analysis_status = data.get("analysis_status")
            if fulfilment_status == "READY" or analysis_status == "COMPLETED":
                return None
            if fulfilment_status == "PROCESSING" or analysis_status == "PROCESSING":
                return None
            if fulfilment_status == "PROCESSING_FAILED" and not retry_failed:
                return None
            if fulfilment_status not in {"VALIDATED", "PROCESSING_FAILED"}:
                return None
            data.update(
                {
                    "fulfilment_status": "PROCESSING",
                    "analysis_status": "PROCESSING",
                    "analysis_started_at": claimed_at,
                    "analysis_completed_at": None,
                    "analysis_error_code": None,
                    "result_status": "NOT_READY",
                    "updated_at": claimed_at,
                }
            )
            transaction.set(order_ref, data, merge=True)
            return Order(**data)

        transaction = self.client.transaction()
        return firestore.transactional(claim)(transaction)

    def save_customer_session(self, session: CustomerSession) -> CustomerSession:
        self.client.collection("customer_sessions").document(session.session_hash).set(session.to_dict(), merge=True)
        return session

    def get_customer_session(self, session_hash: str) -> CustomerSession | None:
        snapshot = self.client.collection("customer_sessions").document(session_hash).get()
        if not snapshot.exists:
            return None
        return CustomerSession(**snapshot.to_dict())

    def delete_customer_session(self, session_hash: str) -> None:
        self.client.collection("customer_sessions").document(session_hash).delete()
