"""Caller owns the transaction. Organization locks serialize all quota transitions.

Only trusted server operations may call this module. No write route exposes it.
"""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ApiError
from app.db.models import Organization, User
from app.domain.usage.models import SubscriptionPeriod, UsageEvent

METERS = (
    "document_uploads",
    "ocr_pages",
    "workflow_runs",
    "input_tokens",
    "output_tokens",
    "cached_tokens",
)


def now():
    return datetime.now(timezone.utc)


def month_bounds(at: datetime):
    start = at.astimezone(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12
        else start.replace(month=start.month + 1)
    )
    return start, end


async def period(
    db: AsyncSession, organization_id: UUID, at: datetime | None = None
) -> SubscriptionPeriod:
    # Lock ordering: document (if any), then organization. Never the reverse.
    found = await db.scalar(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    if found is None:
        raise ApiError(404, "NOT_FOUND", "Organization was not found.")
    start, end = month_bounds(at or now())
    row = await db.scalar(
        select(SubscriptionPeriod)
        .execution_options(populate_existing=True)
        .where(
            SubscriptionPeriod.organization_id == organization_id,
            SubscriptionPeriod.starts_at == start,
        )
    )
    if row is None:
        row = SubscriptionPeriod(
            organization_id=organization_id,
            starts_at=start,
            ends_at=end,
            upload_limit=1000,
            plan="internal_preview",
            status="preview",
        )
        db.add(row)
        await db.flush()
    return row


async def reserve(
    db: AsyncSession,
    *,
    organization_id: UUID,
    user_id: UUID,
    key: str,
    operation: str,
    meter: str,
    quantity: int,
) -> UsageEvent:
    if (
        meter not in METERS
        or type(quantity) is not int
        or quantity < 0
        or not key
        or len(key) > 255
    ):
        raise ValueError("Invalid trusted metering input")
    timestamp = now()
    current = await period(db, organization_id, timestamp)
    owner = await db.scalar(
        select(User.id).where(User.id == user_id, User.organization_id == organization_id)
    )
    if owner is None:
        raise ApiError(404, "NOT_FOUND", "Account was not found.")
    existing = await db.scalar(
        select(UsageEvent)
        .execution_options(populate_existing=True)
        .where(UsageEvent.organization_id == organization_id, UsageEvent.idempotency_key == key)
    )
    if existing:
        if (existing.user_id, existing.operation, existing.meter, existing.quantity) != (
            user_id,
            operation,
            meter,
            quantity,
        ):
            raise ApiError(
                409, "USAGE_KEY_REUSED", "Metering key belongs to a different operation."
            )
        return existing
    if meter == "document_uploads":
        used = await db.scalar(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.period_id == current.id,
                UsageEvent.meter == meter,
                UsageEvent.state.in_(("reserved", "succeeded")),
            )
        )
        if used + quantity > current.upload_limit:
            raise ApiError(
                409, "USAGE_LIMIT_EXCEEDED", "Organization upload allowance is exhausted."
            )
    else:
        # Prevent unconfigured provider or OCR execution from silently bypassing limits.
        raise ApiError(503, "METER_UNAVAILABLE", "This operation has no configured entitlement.")
    row = UsageEvent(
        organization_id=organization_id,
        user_id=user_id,
        period_id=current.id,
        idempotency_key=key,
        operation=operation,
        meter=meter,
        quantity=quantity,
        state="reserved",
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(row)
    await db.flush()
    return row


async def settle(
    db: AsyncSession,
    *,
    organization_id: UUID,
    event_id: UUID,
    state: str,
    error_code: str | None = None,
) -> UsageEvent:
    if state not in {"succeeded", "failed", "released"} or (
        state != "failed" and error_code is not None
    ):
        raise ValueError("Invalid settlement")
    await db.scalar(
        select(Organization.id).where(Organization.id == organization_id).with_for_update()
    )
    row = await db.scalar(
        select(UsageEvent)
        .execution_options(populate_existing=True)
        .where(UsageEvent.id == event_id, UsageEvent.organization_id == organization_id)
        .with_for_update()
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Usage reservation was not found.")
    if row.state != "reserved":
        if row.state == state and row.error_code == error_code:
            return row
        raise ApiError(409, "USAGE_ALREADY_SETTLED", "Usage reservation is already settled.")
    row.state, row.error_code, row.updated_at = state, error_code, now()
    await db.flush()
    return row
