"""Human review API. No general-purpose status setter or client verified flag."""

from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.core.api import success_response
from app.core.auth_context import AuthContext, require_capability
from app.core.errors import ApiError
from app.core.policy import Action
from app.domain.documents.routes import DB, Read, origin, storage
from app.domain.documents.schemas import ERROR_RESPONSES, Success
from app.domain.reviews import service
from app.domain.reviews.schemas import (
    CorrectLayout,
    DecideLayout,
    HistoryQuery,
    ReviewChanged,
    ReviewEventReference,
    ReviewHistory,
    ReviewState,
)

REVIEW_RESPONSES = cast(dict[int | str, dict[str, Any]], ERROR_RESPONSES)

router = APIRouter(prefix="/layout-jobs", tags=["human layout review"], responses=REVIEW_RESPONSES)
Reviewer = Annotated[AuthContext, Depends(require_capability(Action.DOCUMENT_REVIEW))]


def idempotency_key(request):
    keys = request.headers.getlist("idempotency-key")
    key = keys[0].strip() if len(keys) == 1 else ""
    if not 16 <= len(key) <= 255:
        raise ApiError(400, "VALIDATION_ERROR", "A valid Idempotency-Key header is required.")
    return key


@router.get("/{job_id}/review", response_model=Success[ReviewState])
async def get_review(job_id: UUID, request: Request, context: Read, db: DB):
    return success_response(request, await service.get(db, context, job_id, storage(request)))


@router.get("/{job_id}/review/history", response_model=Success[ReviewHistory])
async def get_history(
    job_id: UUID, request: Request, context: Read, db: DB, query: Annotated[HistoryQuery, Query()]
):
    return success_response(
        request, await service.history(db, context, job_id, query, storage(request))
    )


@router.post(
    "/{job_id}/review/corrections",
    dependencies=[Depends(origin)],
    response_model=Success[ReviewChanged],
)
async def correct(job_id: UUID, data: CorrectLayout, request: Request, context: Reviewer, db: DB):
    review, replayed = await service.change(
        db,
        context,
        job_id,
        data,
        idempotency_key(request),
        request.state.request_id,
        storage(request),
    )
    return success_response(request, {"review": review, "replayed": replayed})


@router.post(
    "/{job_id}/review/decisions",
    dependencies=[Depends(origin)],
    response_model=Success[ReviewChanged],
)
async def decide(job_id: UUID, data: DecideLayout, request: Request, context: Reviewer, db: DB):
    review, replayed = await service.change(
        db,
        context,
        job_id,
        data,
        idempotency_key(request),
        request.state.request_id,
        storage(request),
    )
    return success_response(request, {"review": review, "replayed": replayed})


reference_router = APIRouter(
    prefix="/layout-review-events", tags=["human layout review"], responses=REVIEW_RESPONSES
)


@reference_router.get("/{event_id}", response_model=Success[ReviewEventReference])
async def review_event(event_id: UUID, request: Request, context: Read, db: DB):
    return success_response(
        request, await service.event_reference(db, context, event_id, storage(request))
    )
