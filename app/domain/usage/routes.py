"""Read-only usage API. Scope always derives from the authenticated account."""

from datetime import datetime, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api import success_response
from app.core.auth_context import AuthContext, require_capability
from app.core.errors import ApiError
from app.core.policy import Action
from app.db.base import get_db_session
from app.domain.usage.models import SubscriptionPeriod, UsageEvent
from app.domain.usage.pagination import decode, encode
from app.domain.usage.service import METERS, month_bounds, now

router = APIRouter(prefix="/usage", tags=["usage"])
DB = Annotated[AsyncSession, Depends(get_db_session)]
Own = Annotated[AuthContext, Depends(require_capability(Action.USAGE_READ))]
Org = Annotated[AuthContext, Depends(require_capability(Action.USAGE_ORGANIZATION_READ))]


def bounds(start: datetime | None, end: datetime | None):
    end = end or now()
    start = start or end - timedelta(days=30)
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or end <= start
        or end - start > timedelta(days=93)
    ):
        raise ApiError(
            400,
            "INVALID_USAGE_RANGE",
            "Use timezone-aware start/end with a range of at most 93 days.",
        )
    return start, end


def filters(context, own, start, end, user_id=None):
    conditions = [
        UsageEvent.organization_id == context.organization_id,
        UsageEvent.created_at >= start,
        UsageEvent.created_at < end,
    ]
    if own:
        conditions.append(UsageEvent.user_id == context.user_id)
    elif user_id:
        conditions.append(UsageEvent.user_id == user_id)
    return conditions


async def summary(request, context, db, own, start, end):
    start, end = bounds(start, end)
    conditions = filters(context, own, start, end)
    rows = (
        await db.execute(
            select(UsageEvent.meter, UsageEvent.state, func.sum(UsageEvent.quantity), func.count())
            .where(*conditions)
            .group_by(UsageEvent.meter, UsageEvent.state)
        )
    ).all()
    meters = {
        meter: {
            "used": 0 if meter == "document_uploads" else None,
            "reserved": 0 if meter == "document_uploads" else None,
            "available": meter == "document_uploads",
        }
        for meter in METERS
    }
    states = {state: 0 for state in ("reserved", "succeeded", "failed", "released")}
    for meter, state, quantity, count in rows:
        states[state] += count
        if state == "succeeded":
            meters[meter]["used"] = quantity
        elif state == "reserved":
            meters[meter]["reserved"] = quantity
    pstart, pend = month_bounds(now())
    current = await db.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.organization_id == context.organization_id,
            SubscriptionPeriod.starts_at == pstart,
        )
    )
    limit = current.upload_limit if current else 1000
    consumed = await db.scalar(
        select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.organization_id == context.organization_id,
            UsageEvent.period_id == current.id if current else False,
            UsageEvent.meter == "document_uploads",
            UsageEvent.state.in_(("succeeded", "reserved")),
        )
    )
    groups = []
    if not own:
        grouped = (
            await db.execute(
                select(
                    UsageEvent.user_id,
                    UsageEvent.operation,
                    UsageEvent.meter,
                    UsageEvent.state,
                    func.count().label("events"),
                    func.sum(UsageEvent.quantity).label("quantity"),
                )
                .where(*conditions)
                .group_by(
                    UsageEvent.user_id, UsageEvent.operation, UsageEvent.meter, UsageEvent.state
                )
                .order_by(
                    UsageEvent.user_id, UsageEvent.operation, UsageEvent.meter, UsageEvent.state
                )
                .limit(101)
            )
        ).all()
        groups = [
            {
                "userId": str(r.user_id),
                "operation": r.operation,
                "meter": r.meter,
                "state": r.state,
                "events": r.events,
                "quantity": r.quantity,
            }
            for r in grouped[:100]
        ]
    return success_response(
        request,
        {
            "scope": "user" if own else "organization",
            "organizationId": str(context.organization_id),
            "userId": str(context.user_id) if own else None,
            "start": start,
            "end": end,
            "meters": meters,
            "states": states,
            "cost": {"amount": None, "currency": None, "pricingVersion": None, "available": False},
            "subscription": {
                "plan": current.plan if current else "internal_preview",
                "status": current.status if current else "preview",
                "billingAvailable": False,
                "scope": "organization",
                "periodStart": pstart,
                "periodEnd": pend,
                "limits": {
                    "document_uploads": {
                        "limit": limit,
                        "usedAndReserved": consumed,
                        "remaining": max(0, limit - consumed),
                    },
                    **{m: None for m in METERS if m != "document_uploads"},
                },
            },
            "groups": groups,
            "groupsTruncated": not own and len(grouped) > 100,
        },
    )


def record(row):
    return {
        "id": str(row.id),
        "userId": str(row.user_id),
        "operation": row.operation,
        "meter": row.meter,
        "quantity": row.quantity,
        "state": row.state,
        "errorCode": row.error_code,
        "amount": str(row.amount) if row.amount is not None else None,
        "currency": row.currency,
        "pricingVersion": row.pricing_version,
        "createdAt": row.created_at.replace(tzinfo=timezone.utc)
        if row.created_at.tzinfo is None
        else row.created_at,
        "updatedAt": row.updated_at.replace(tzinfo=timezone.utc)
        if row.updated_at.tzinfo is None
        else row.updated_at,
    }


async def history(request, context, db, own, start, end, limit, cursor, user_id, operation, state):
    start, end = bounds(start, end)
    conditions = filters(context, own, start, end, user_id)
    if operation:
        conditions.append(UsageEvent.operation == operation)
    if state:
        if state not in {"reserved", "succeeded", "failed", "released"}:
            raise ApiError(400, "INVALID_USAGE_STATE", "Unknown usage state.")
        conditions.append(UsageEvent.state == state)
    scope = [
        str(context.organization_id),
        str(context.user_id) if own else str(user_id) if user_id else None,
        own,
        start.isoformat(),
        end.isoformat(),
        operation,
        state,
    ]
    if cursor:
        at, identity = decode(cursor, scope)
        conditions.append(
            or_(
                UsageEvent.created_at < at,
                and_(UsageEvent.created_at == at, UsageEvent.id < identity),
            )
        )
    rows = list(
        await db.scalars(
            select(UsageEvent)
            .where(*conditions)
            .order_by(UsageEvent.created_at.desc(), UsageEvent.id.desc())
            .limit(limit + 1)
        )
    )
    return success_response(
        request,
        {
            "items": [record(row) for row in rows[:limit]],
            "nextCursor": encode(scope, rows[limit - 1]) if len(rows) > limit else None,
            "start": start,
            "end": end,
        },
    )


@router.get("/me")
async def my_summary(
    request: Request,
    context: Own,
    db: DB,
    start: datetime | None = None,
    end: datetime | None = None,
):
    return await summary(request, context, db, True, start, end)


@router.get("/organization")
async def organization_summary(
    request: Request,
    context: Org,
    db: DB,
    start: datetime | None = None,
    end: datetime | None = None,
):
    return await summary(request, context, db, False, start, end)


@router.get("/me/history")
async def my_history(
    request: Request,
    context: Own,
    db: DB,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    operation: str | None = Query(None, max_length=64),
    state: str | None = None,
):
    return await history(
        request, context, db, True, start, end, limit, cursor, None, operation, state
    )


@router.get("/organization/history")
async def organization_history(
    request: Request,
    context: Org,
    db: DB,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(25, ge=1, le=100),
    cursor: str | None = Query(None, max_length=2048),
    userId: UUID | None = None,
    operation: str | None = Query(None, max_length=64),
    state: str | None = None,
):
    return await history(
        request, context, db, False, start, end, limit, cursor, userId, operation, state
    )
