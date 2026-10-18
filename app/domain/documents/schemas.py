"""Version-one browser contracts. Metadata never attests that bytes are safe."""

import unicodedata
from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BYTES = 25 * 1024 * 1024
DocumentStatus = Literal["received", "processing", "review_required", "completed", "failed"]
MediaType = Literal["application/pdf", "image/png", "image/jpeg"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateDocument(Contract):
    branchId: UUID | None = None
    departmentId: UUID | None = None
    source: Literal["upload", "email", "api", "integration"] = "upload"
    externalReference: str | None = Field(default=None, min_length=1, max_length=255)
    originalFileName: str = Field(min_length=1, max_length=255)
    mediaType: MediaType
    sizeBytes: int = Field(strict=True, gt=0, le=MAX_BYTES)
    checksumSha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("originalFileName")
    @classmethod
    def filename(cls, value):
        if (
            value != value.strip()
            or value in {".", ".."}
            or any(c in "/\\:" or unicodedata.category(c).startswith("C") for c in value)
        ):
            raise ValueError("A plain filename is required")
        return value

    @field_validator("externalReference")
    @classmethod
    def reference(cls, value):
        if value is not None and (not value.strip() or value != value.strip()):
            raise ValueError("A nonempty reference is required")
        return value


class DocumentRecord(CreateDocument):
    id: UUID
    organizationId: UUID
    status: DocumentStatus
    failureCode: str | None
    receivedAt: datetime
    updatedAt: datetime
    # Additive field: clients must never infer safe/processed from upload success.
    scanStatus: Literal["awaiting_upload", "pending_scan", "clean", "rejected"]


class DocumentList(Contract):
    items: list[DocumentRecord]
    nextCursor: str | None


class ListQuery(Contract):
    status: DocumentStatus | None = None
    cursor: str | None = Field(default=None, min_length=1, max_length=256)
    limit: int = Field(default=50, ge=1, le=100)


class UploadAttempt(Contract):
    id: UUID
    documentId: UUID
    status: Literal["initiated", "completed", "failed"]
    createdAt: datetime
    completedAt: datetime | None
    updatedAt: datetime
    failureCode: str | None


class UploadTarget(Contract):
    uploadUrl: str
    expiresAt: datetime
    requiredHeaders: dict[str, str]


class InitiatedUpload(Contract):
    attempt: UploadAttempt
    target: UploadTarget
    replayed: bool


class CompletedUpload(Contract):
    attempt: UploadAttempt
    replayed: bool


class CreateExtraction(Contract):
    schemaVersion: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")


class Meta(Contract):
    requestId: str
    timestamp: datetime


T = TypeVar("T")


class Success(Contract, Generic[T]):
    data: T
    meta: Meta


class ErrorDetail(Contract):
    code: str
    message: str
    requestId: str
    fieldErrors: dict[str, list[str]] | None = None


class Failure(Contract):
    error: ErrorDetail
    meta: Meta


ERROR_RESPONSES = {
    code: {"model": Failure} for code in (400, 401, 403, 404, 408, 409, 413, 415, 429, 503)
}
