"""Organization-only administrative reads and version-guarded role changes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api import success_response
from app.core.auth_context import AuthContext, require_capability
from app.core.policy import Action
from app.db.base import get_db_session
from app.db.models import Organization
from app.domain.organization import service
from app.domain.organization.schemas import ChangeRole, MemberQuery
from app.domain.workflows.routes import trusted_origin

router = APIRouter(prefix="/organization", tags=["organization administration"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Read = Annotated[AuthContext, Depends(require_capability(Action.MEMBERS_READ))]
Write = Annotated[AuthContext, Depends(require_capability(Action.MEMBERS_MANAGE))]
HierarchyRead = Annotated[AuthContext, Depends(require_capability(Action.HIERARCHY_READ))]


@router.get("/members")
async def members(request: Request, context: Read, db: DB, query: Annotated[MemberQuery, Query()]):
    return success_response(request, await service.listing(db, context, query))


@router.get("/members/{identity}")
async def member(identity: UUID, request: Request, context: Read, db: DB):
    return success_response(request, service.public(await service.get(db, context, identity)))


@router.patch("/members/{identity}/role", dependencies=[Depends(trusted_origin)])
async def role(identity: UUID, data: ChangeRole, request: Request, context: Write, db: DB):
    changed = await service.change_role(db, context, identity, data, request.state.request_id)
    return success_response(request, service.public(changed))


@router.get("/hierarchy")
async def hierarchy(request: Request, context: HierarchyRead, db: DB):
    organization = await db.get(Organization, context.organization_id)
    return success_response(
        request,
        {
            "organization": {
                "id": str(organization.id),
                "name": organization.name,
                "accountType": organization.account_type,
            },
            # The Python identity model has no branch/department/persona tables.
            "branches": [],
            "departments": [],
            "personas": [],
        },
    )
