"""Tenant-only roster with serialized authorization and administrator invariants."""

from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth_context import AuthContext
from app.core.errors import ApiError
from app.core.policy import Action, require_action
from app.core.rbac import Role
from app.db.models import Organization, User
from app.domain.audit.service import record
from app.domain.organization.schemas import ChangeRole, MemberQuery
from app.domain.workflows.service import timestamp

ADMIN_ROLES = {Role.TENANT_ADMIN.value, Role.ORGANIZATION_ADMIN.value}


def public(member: User) -> dict:
    return {
        "id": str(member.id),
        "organizationId": str(member.organization_id),
        "displayName": member.display_name,
        "email": member.email,
        "role": member.role,
        "version": member.version,
        "createdAt": timestamp(member.created_at).isoformat(),
    }


async def get(db: AsyncSession, context: AuthContext, identity: UUID) -> User:
    member = await db.scalar(
        select(User)
        .where(User.organization_id == context.organization_id, User.id == identity)
        .execution_options(populate_existing=True)
    )
    if member is None:
        raise ApiError(404, "NOT_FOUND", "Member was not found.")
    return member


async def listing(db: AsyncSession, context: AuthContext, query: MemberQuery) -> dict:
    statement = select(User).where(User.organization_id == context.organization_id)
    if query.cursor:
        try:
            position = UUID(query.cursor)
        except ValueError:
            raise ApiError(400, "INVALID_CURSOR", "Member cursor is invalid.") from None
        statement = statement.where(User.id > position)
    rows = list(await db.scalars(statement.order_by(User.id).limit(query.limit + 1)))
    visible = rows[: query.limit]
    return {
        "items": [public(member) for member in visible],
        "nextCursor": str(visible[-1].id) if len(rows) > query.limit else None,
    }


async def change_role(
    db: AsyncSession, context: AuthContext, identity: UUID, data: ChangeRole, request_id: str
) -> User:
    # All role writers take the same tenant lock before re-reading authorization.
    # This prevents reciprocal demotions and stale in-flight administrative access.
    organization = await db.scalar(
        select(Organization)
        .where(Organization.id == context.organization_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if organization is None:
        raise ApiError(404, "NOT_FOUND", "Organization was not found.")
    actor = await get(db, context, context.user_id)
    try:
        role = Role(actor.role)
    except ValueError:
        raise ApiError(403, "FORBIDDEN", "Insufficient permissions.") from None
    fresh = AuthContext(actor.id, actor.organization_id, role, organization.account_type)
    require_action(fresh, Action.MEMBERS_MANAGE)
    member = await get(db, fresh, identity)
    if member.id == actor.id:
        raise ApiError(403, "SELF_ROLE_CHANGE_FORBIDDEN", "You cannot change your own role.")
    if member.role == Role.PLATFORM_ADMIN:
        raise ApiError(403, "FORBIDDEN", "Platform administrator roles cannot be changed here.")
    if member.version != data.expectedVersion:
        raise ApiError(409, "MEMBER_CONFLICT", "Member changed. Reload before trying again.")
    if member.role in ADMIN_ROLES and data.role not in ADMIN_ROLES:
        remaining = await db.scalar(
            select(User.id)
            .where(
                User.organization_id == fresh.organization_id,
                User.id != member.id,
                User.role.in_(ADMIN_ROLES),
            )
            .limit(1)
        )
        if remaining is None:
            raise ApiError(409, "LAST_ORGANIZATION_ADMIN", "Keep at least one organization admin.")
    if member.role == data.role:
        return member
    changed = await db.scalar(
        update(User)
        .where(
            User.organization_id == fresh.organization_id,
            User.id == identity,
            User.version == data.expectedVersion,
        )
        .values(role=data.role, version=User.version + 1)
        .returning(User)
        .execution_options(populate_existing=True)
    )
    if changed is None:
        raise ApiError(409, "MEMBER_CONFLICT", "Member changed. Reload before trying again.")
    await record(
        db,
        organization_id=fresh.organization_id,
        actor_user_id=fresh.user_id,
        action="organization.member.role_updated",
        resource_id=changed.id,
        request_id=request_id,
    )
    await db.commit()
    return changed
