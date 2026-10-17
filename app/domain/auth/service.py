from datetime import timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.authentication import AuthenticationService
from app.core.errors import ApiError
from app.core.rbac import Role
from app.core.session import SessionManager
from app.db.models import Organization, Session, User
from app.domain.auth.schemas import RegisterRequest


async def issue_session(db: AsyncSession, user: User, ttl: int) -> tuple[str, Session]:
    token, digest, expiry = SessionManager(ttl).issue(user.id, user.organization_id)
    session = Session(
        user_id=user.id,
        organization_id=user.organization_id,
        token_digest=digest,
        expires_at=expiry,
    )
    db.add(session)
    await db.flush()
    return token, session


async def register(db: AsyncSession, data: RegisterRequest, ttl: int):
    email = str(data.email).lower()
    existing = await db.scalar(select(User.id).where(func.lower(User.email) == email).limit(1))
    if existing:
        raise ApiError(409, "EMAIL_ALREADY_REGISTERED", "An account already exists for this email.")
    password_hash = await run_in_threadpool(AuthenticationService.hash_password, data.password)
    organization = Organization(name=data.organization_name)
    db.add(organization)
    await db.flush()
    user = User(
        organization_id=organization.id,
        display_name=data.display_name,
        email=email,
        password_hash=password_hash,
        role=Role.ORGANIZATION_ADMIN,
    )
    db.add(user)
    try:
        await db.flush()
        token, session = await issue_session(db, user, ttl)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        # Only the named email uniqueness violation is a registration conflict.
        message = str(exc.orig)
        if "uq_users_email" in message or "UNIQUE constraint failed: users.email" in message:
            raise ApiError(
                409, "EMAIL_ALREADY_REGISTERED", "An account already exists for this email."
            ) from exc
        raise
    return token, session_payload(user, organization, session)


def session_payload(user: User, organization: Organization, session: Session) -> dict:
    role = Role(user.role)  # Unknown persisted roles fail closed.
    wire_roles = {
        Role.PLATFORM_ADMIN: "super_admin",
        Role.TENANT_ADMIN: "admin",
        Role.ORGANIZATION_ADMIN: "admin",
    }
    expiry = session.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return {
        "user": {
            "id": str(user.id),
            "email": user.email,
            "displayName": user.display_name,
            "role": wire_roles.get(role, role.value),
            "organizationId": str(organization.id),
            "organizationName": organization.name,
        },
        "expiresAt": expiry.isoformat(),
    }
