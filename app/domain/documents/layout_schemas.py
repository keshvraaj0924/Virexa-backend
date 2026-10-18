"""Layout is untrusted extracted content, not an invoice or ERP payload."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LayoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pipelineVersion: Literal["docling-local-v1"] = "docling-local-v1"


class LayoutStatus(BaseModel):
    id: UUID
    documentId: UUID
    state: Literal["pending", "running", "needs_review", "error"]
    pipelineVersion: str
    sourceSha256: str
    pageCount: int | None
    failureCode: str | None
    createdAt: datetime
    updatedAt: datetime


class LayoutList(BaseModel):
    items: list[LayoutStatus]


class LayoutRequested(BaseModel):
    job: LayoutStatus
    replayed: bool


class LayoutResult(BaseModel):
    resultVersion: Literal["layout-result-v1"]
    reviewStatus: Literal["needs_review"]
    sourceSha256: str
    pipelineVersion: str
    parserVersion: str
    modelVersion: str
    pageCount: int
    sourcePreviewAvailable: bool
    warnings: list[str]
    canonical: dict
