from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


class UploadStorageError(Exception):
    pass


@dataclass(frozen=True)
class StoredUpload:
    object_path: str
    size_bytes: int


class UploadStorage(Protocol):
    def save(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        ...

    def delete(self, object_path: str) -> None:
        ...

    def load(self, object_path: str) -> bytes:
        ...

    def save_result(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        ...

    def save_final_result(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        ...


def build_upload_object_path(order_id: str, extension: str) -> str:
    safe_ext = extension.lower().lstrip(".")
    return f"uploads/{order_id}/{uuid.uuid4().hex}.{safe_ext}"


def build_result_object_path(order_id: str, extension: str) -> str:
    safe_ext = extension.lower().lstrip(".")
    return f"results/{order_id}/{uuid.uuid4().hex}.{safe_ext}"


def build_final_result_object_path(order_id: str, extension: str) -> str:
    safe_ext = extension.lower().lstrip(".")
    return f"results/{order_id}/final/{uuid.uuid4().hex}.{safe_ext}"


class InMemoryUploadStorage:
    def __init__(self, fail_save: bool = False, fail_delete: bool = False) -> None:
        self.fail_save = fail_save
        self.fail_delete = fail_delete
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def save(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        if self.fail_save:
            raise UploadStorageError("UPLOAD_STORAGE_SAVE_FAILED")
        object_path = build_upload_object_path(order_id, extension)
        self.objects[object_path] = content
        return StoredUpload(object_path=object_path, size_bytes=len(content))

    def delete(self, object_path: str) -> None:
        if self.fail_delete:
            raise UploadStorageError("UPLOAD_STORAGE_DELETE_FAILED")
        self.objects.pop(object_path, None)
        self.deleted.append(object_path)

    def load(self, object_path: str) -> bytes:
        try:
            return self.objects[object_path]
        except KeyError as exc:
            raise UploadStorageError("STORAGE_OBJECT_MISSING") from exc

    def save_result(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        if self.fail_save:
            raise UploadStorageError("RESULT_STORAGE_SAVE_FAILED")
        object_path = build_result_object_path(order_id, extension)
        self.objects[object_path] = content
        return StoredUpload(object_path=object_path, size_bytes=len(content))

    def save_final_result(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        if self.fail_save:
            raise UploadStorageError("FINAL_RESULT_STORAGE_SAVE_FAILED")
        object_path = build_final_result_object_path(order_id, extension)
        self.objects[object_path] = content
        return StoredUpload(object_path=object_path, size_bytes=len(content))


class GoogleCloudUploadStorage:
    def __init__(self, bucket_name: str, project: str | None = None) -> None:
        if not bucket_name:
            raise UploadStorageError("UPLOAD_BUCKET_NOT_CONFIGURED")
        from google.cloud import storage

        self.bucket = storage.Client(project=project).bucket(bucket_name)

    def save(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        object_path = build_upload_object_path(order_id, extension)
        blob = self.bucket.blob(object_path)
        blob.upload_from_string(content, content_type=content_type)
        return StoredUpload(object_path=object_path, size_bytes=len(content))

    def delete(self, object_path: str) -> None:
        self.bucket.blob(object_path).delete()

    def load(self, object_path: str) -> bytes:
        blob = self.bucket.blob(object_path)
        if not blob.exists():
            raise UploadStorageError("STORAGE_OBJECT_MISSING")
        return blob.download_as_bytes()

    def save_result(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        object_path = build_result_object_path(order_id, extension)
        blob = self.bucket.blob(object_path)
        blob.upload_from_string(content, content_type=content_type)
        return StoredUpload(object_path=object_path, size_bytes=len(content))

    def save_final_result(
        self,
        *,
        order_id: str,
        content: bytes,
        extension: str,
        content_type: str,
    ) -> StoredUpload:
        object_path = build_final_result_object_path(order_id, extension)
        blob = self.bucket.blob(object_path)
        blob.upload_from_string(content, content_type=content_type)
        return StoredUpload(object_path=object_path, size_bytes=len(content))
