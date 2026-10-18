from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api import success_response
from app.core.auth_context import AuthContext, require_capability
from app.core.errors import ApiError
from app.core.policy import Action
from app.db.base import get_db_session
from app.domain.workflows import service
from app.domain.workflows.schemas import CreateWorkflow, ListQuery, UpdateWorkflow

router = APIRouter(prefix="/workflows", tags=["workflow records"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Read = Annotated[AuthContext, Depends(require_capability(Action.WORKFLOW_READ))]
Write = Annotated[AuthContext, Depends(require_capability(Action.WORKFLOW_CREATE))]


def trusted_origin(request: Request):
    if request.headers.get("origin") not in request.app.state.settings.origins:
        raise ApiError(403, "UNTRUSTED_ORIGIN", "Request origin is not trusted.")


@router.get("")
async def list_workflows(
    request: Request, context: Read, db: DB, query: Annotated[ListQuery, Query()]
):
    return success_response(request, await service.listing(db, context, query))


@router.get("/{identity}")
async def get_workflow(identity: UUID, request: Request, context: Read, db: DB):
    return success_response(request, service.public(await service.get(db, context, identity)))


@router.post("", dependencies=[Depends(trusted_origin)])
async def create_workflow(
    data: CreateWorkflow, request: Request, response: Response, context: Write, db: DB
):
    keys = request.headers.getlist("idempotency-key")
    key = keys[0].strip() if len(keys) == 1 else ""
    if not 16 <= len(key) <= 255:
        raise ApiError(400, "VALIDATION_ERROR", "A valid Idempotency-Key header is required.")
    row, replayed = await service.create(db, context, data, key, request.state.request_id)
    response.status_code = 200 if replayed else 201
    return success_response(request, service.public(row))


@router.patch("/{identity}", dependencies=[Depends(trusted_origin)])
async def update_workflow(
    identity: UUID, data: UpdateWorkflow, request: Request, context: Write, db: DB
):
    row = await service.change(db, context, identity, data, request.state.request_id)
    return success_response(request, service.public(row))
