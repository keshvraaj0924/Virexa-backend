"""Plain text corrections only. No arbitrary JSON Patch or executable instructions."""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReviewVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expectedRevision: int = Field(strict=True, ge=0)
    sourceSha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    resultKind: Literal["layout"]


class TextCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nodeId: str = Field(pattern=r"^#/texts/[0-9]{1,6}$", max_length=32)
    text: str = Field(max_length=4000)


class CorrectLayout(ReviewVersion):
    corrections: list[TextCorrection] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_nodes(self):
        if len(json.dumps(self.model_dump(), ensure_ascii=False).encode()) > 131072:
            raise ValueError("Correction payload exceeds 128 KiB")
        if len({item.nodeId for item in self.corrections}) != len(self.corrections):
            raise ValueError("Each node may be corrected only once per request")
        return self


class DecideLayout(ReviewVersion):
    decision: Literal["approve", "reject"]
    reasonCode: Literal[
        "verified_against_source", "incorrect_layout", "incomplete_layout", "unreadable_source"
    ]
    sourceCompared: bool = Field(strict=True)

    @model_validator(mode="after")
    def matching_reason(self):
        if self.decision == "approve" and self.sourceCompared is not True:
            raise ValueError("Approval requires explicit comparison with the source")
        if (self.decision == "approve") != (self.reasonCode == "verified_against_source"):
            raise ValueError("Decision and reason must agree")
        return self


class HistoryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=25, ge=1, le=100)
    cursor: int | None = Field(default=None, ge=1)


class ReviewState(BaseModel):
    jobId: str
    documentId: str
    resultKind: Literal["layout"]
    sourceSha256: str
    revision: int
    status: Literal["needs_review", "corrected", "approved", "rejected"]
    corrections: list[TextCorrection]
    current: bool
    ready: bool
    scope: Literal["layout_only"]
    eligible: bool


class ReviewChanged(BaseModel):
    review: ReviewState
    replayed: bool


class ReviewHistoryItem(BaseModel):
    id: str
    revision: int
    actorUserId: str
    createdAt: str
    status: Literal["corrected", "approved", "rejected"]
    reasonCode: str
    sourceCompared: bool
    sourceSha256: str
    resultSha256: str
    resultKind: Literal["layout"]
    correctedNodeCount: int


class ReviewHistory(BaseModel):
    items: list[ReviewHistoryItem]
    nextCursor: int | None


class ReviewEventReference(ReviewHistoryItem):
    jobId: str
    documentId: str
    currentCandidate: bool
