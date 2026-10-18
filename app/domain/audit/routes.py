"""Bounded tenant-only read API compatible with the Node event representation."""

from datetime import timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api import success_response
from app.core.auth_context import AuthContext, require_capability
from app.core.errors import ApiError
from app.core.policy import Action
from app.db.base import get_db_session
from app.domain.audit.models import AuditEvent
from app.domain.audit.service import ACTIONS
from app.domain.usage.pagination import decode, encode

router = APIRouter(prefix="/audit", tags=["audit"])


class AuditQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=2048)
    action: str | None = Field(default=None, max_length=120)
    outcome: str | None = Field(default=None, pattern="^(succeeded|failed|denied)$")


def public_event(row):
    at = row.created_at
    return {
        "id": str(row.id),
        "organizationId": str(row.organization_id),
        "actorUserId": str(row.actor_user_id) if row.actor_user_id else None,
        "action": row.action,
        "resourceType": row.resource_type,
        "resourceId": row.resource_id,
        "requestId": row.request_id,
        # Never expose arbitrary metadata from a legacy writer.
        "metadata": {"outcome": ACTIONS[row.action][1]},
        "createdAt": (at if at.tzinfo else at.replace(tzinfo=timezone.utc)).isoformat(),
    }


@router.get("/events")
async def events(
    request: Request,
    query: Annotated[AuditQuery, Query()],
    context: AuthContext = Depends(require_capability(Action.AUDIT_READ)),
    db: AsyncSession = Depends(get_db_session),
):
    if query.action is not None and query.action not in ACTIONS:
        raise ApiError(400, "VALIDATION_ERROR", "Audit action is invalid.")
    actions = [
        key
        for key, (_, outcome) in ACTIONS.items()
        if (query.action is None or key == query.action)
        and (query.outcome is None or outcome == query.outcome)
    ]
    scope = [str(context.organization_id), "audit-v1", query.action, query.outcome]
    statement = select(AuditEvent).where(
        AuditEvent.organization_id == context.organization_id, AuditEvent.action.in_(actions)
    )
    if query.cursor:
        try:
            at, identity = decode(query.cursor, scope)
        except ApiError:
            raise ApiError(400, "INVALID_CURSOR", "Audit cursor is invalid.") from None
        statement = statement.where(
            or_(
                AuditEvent.created_at < at,
                and_(AuditEvent.created_at == at, AuditEvent.id < identity),
            )
        )
    rows = list(
        await db.scalars(
            statement.order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).limit(
                query.limit + 1
            )
        )
    )
    visible = rows[: query.limit]
    return success_response(
        request,
        {
            "events": [public_event(row) for row in visible],
            "nextCursor": encode(scope, visible[-1]) if len(rows) > query.limit else None,
            "coverage": {
                "mode": "forward_only",
                "version": "python-audit-v1",
                "actions": list(ACTIONS),
                "notice": "Records begin when this audit version is enabled. Earlier activity, failed anonymous authentication, extraction/model jobs and email delivery are not covered. Error-event recording is best effort during database outages.",
            },
        },
    )
