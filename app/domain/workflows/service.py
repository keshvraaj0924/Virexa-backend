"""Atomic metadata changes, with tenant predicates and same-transaction audit."""

import base64
import binascii
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from app.core.errors import ApiError
from app.core.policy import Action, WorkflowAttributes, require_workflow
from app.domain.audit.service import record
from app.domain.workflows.models import Workflow, WorkflowKey


def timestamp(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def public(row):
    return {
        "id": str(row.id),
        "organizationId": str(row.organization_id),
        "createdByUserId": str(row.created_by_user_id),
        "name": row.name,
        "description": row.description,
        "status": row.status,
        "version": row.version,
        "createdAt": timestamp(row.created_at)
        .astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "updatedAt": timestamp(row.updated_at)
        .astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
    }


async def get(db, context, identity):
    row = await db.scalar(
        select(Workflow).where(
            Workflow.organization_id == context.organization_id, Workflow.id == identity
        )
    )
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Workflow was not found.")
    return row


async def create(db, context, data, key, request_id):
    normalized = {"name": data.name, "description": data.description or None}
    digest = hashlib.sha256(
        json.dumps(normalized, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    # The unique reservation serializes competing requests across processes.
    # ON CONFLICT preserves the transaction without a savepoint (SQLite's legacy
    # transaction mode can otherwise commit an outermost released savepoint).
    insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
    inserted = await db.scalar(
        insert(WorkflowKey)
        .values(organization_id=context.organization_id, idempotency_key=key, request_hash=digest)
        .on_conflict_do_nothing(index_elements=["organization_id", "idempotency_key"])
        .returning(WorkflowKey.idempotency_key)
    )
    reservation = await db.scalar(
        select(WorkflowKey)
        .where(
            WorkflowKey.organization_id == context.organization_id,
            WorkflowKey.idempotency_key == key,
        )
        .with_for_update()
    )
    if reservation is None:
        raise ApiError(503, "DEPENDENCY_UNAVAILABLE", "Workflow reservation is unavailable.")
    if reservation.request_hash != digest:
        raise ApiError(
            409,
            "IDEMPOTENCY_KEY_REUSED",
            "The idempotency key was already used with a different request.",
        )
    if inserted is None:
        row = await get(db, context, reservation.workflow_id)
        return row, True
    now = datetime.now(timezone.utc)
    row = Workflow(
        organization_id=context.organization_id,
        created_by_user_id=context.user_id,
        **normalized,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    await db.flush()
    reservation.workflow_id = row.id
    await record(
        db,
        organization_id=context.organization_id,
        actor_user_id=context.user_id,
        action="workflow.created",
        resource_id=row.id,
        request_id=request_id,
    )
    await db.commit()
    return row, False


async def change(db, context, identity, data, request_id):
    row = await get(db, context, identity)
    require_workflow(
        context,
        Action.WORKFLOW_CREATE,
        WorkflowAttributes(row.organization_id, row.created_by_user_id, row.status),
    )
    if row.version != data.expectedVersion:
        raise ApiError(
            409, "WORKFLOW_CONFLICT", "Workflow changed before this update could be applied."
        )
    require_workflow(
        context,
        Action.WORKFLOW_CREATE,
        WorkflowAttributes(row.organization_id, row.created_by_user_id, row.status),
        data.status,
    )
    values = data.model_dump(exclude_unset=True, exclude={"expectedVersion"})
    if "description" in values:
        values["description"] = values["description"] or None
    values.update(version=Workflow.version + 1, updated_at=datetime.now(timezone.utc))
    changed = await db.scalar(
        update(Workflow)
        .where(
            Workflow.organization_id == context.organization_id,
            Workflow.id == identity,
            Workflow.version == data.expectedVersion,
            Workflow.status == row.status,
            Workflow.created_by_user_id == row.created_by_user_id,
        )
        .values(**values)
        .returning(Workflow)
        .execution_options(populate_existing=True)
    )
    if changed is None:
        raise ApiError(
            409, "WORKFLOW_CONFLICT", "Workflow changed before this update could be applied."
        )
    await record(
        db,
        organization_id=context.organization_id,
        actor_user_id=context.user_id,
        action="workflow.updated",
        resource_id=changed.id,
        request_id=request_id,
    )
    await db.commit()
    return changed


def decode_cursor(cursor):
    try:
        value = json.loads(
            base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
        )
        if not isinstance(value, dict) or set(value) != {"createdAt", "id"}:
            raise ValueError()
        at, identity = datetime.fromisoformat(value["createdAt"]), UUID(value["id"])
        if at.tzinfo is None:
            raise ValueError()
        return at, identity
    except (ValueError, TypeError, binascii.Error, AttributeError):
        raise ApiError(400, "INVALID_CURSOR", "Workflow cursor is invalid.") from None


async def listing(db, context, query):
    statement = select(Workflow).where(Workflow.organization_id == context.organization_id)
    if query.status:
        statement = statement.where(Workflow.status == query.status)
    if query.cursor:
        at, identity = decode_cursor(query.cursor)
        statement = statement.where(
            or_(Workflow.created_at < at, and_(Workflow.created_at == at, Workflow.id < identity))
        )
    rows = list(
        await db.scalars(
            statement.order_by(Workflow.created_at.desc(), Workflow.id.desc()).limit(
                query.limit + 1
            )
        )
    )
    visible, cursor = rows[: query.limit], None
    if len(rows) > query.limit:
        last = visible[-1]
        position = {"createdAt": timestamp(last.created_at).isoformat(), "id": str(last.id)}
        cursor = base64.urlsafe_b64encode(json.dumps(position).encode()).decode().rstrip("=")
    return {"items": [public(row) for row in visible], "nextCursor": cursor}
