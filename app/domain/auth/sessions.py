"""Owner-scoped session inventory and serialized revocation for the Python candidate."""

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api import success_response
from app.core.auth_context import AuthContext
from app.core.authentication import AuthenticationService
from app.core.errors import ApiError
from app.core.policy import Action, SessionAttributes, require_session
from app.core.rbac import Role
from app.core.session import SessionManager
from app.db.base import get_db_session
from app.db.models import Session, User

router = APIRouter(prefix="/auth/sessions", tags=["sessions"])
authentication = AuthenticationService()


def trusted_origin(request: Request):
    if request.headers.get("origin") not in request.app.state.settings.origins:
        raise ApiError(403, "UNTRUSTED_ORIGIN", "Request origin is not trusted.")


async def resolve_owner(request: Request, db: AsyncSession, *, lock: bool = False):
    token = request.cookies.get(request.app.state.settings.session_cookie_name, "")
    resolved = await authentication.resolve_session(db, token)
    if resolved is not None and lock:
        # Serialize every lifecycle mutation on the owner, then re-read live authority.
        # Locking individual session rows permits mutual-revocation deadlocks.
        await db.execute(select(User.id).where(User.id == resolved[0].id).with_for_update())
        resolved = await authentication.resolve_session(db, token)
    if resolved is None:
        raise ApiError(401, "UNAUTHENTICATED", "Authentication is required.")
    user, session = resolved
    if user.role not in {role.value for role in Role}:
        raise ApiError(403, "FORBIDDEN", "Invalid account role.")
    return user, session


def subject(user: User) -> AuthContext:
    return AuthContext(user.id, user.organization_id, Role(user.role))


def authorize_session(user: User, action: Action, target: Session) -> None:
    require_session(
        subject(user),
        action,
        SessionAttributes(
            target.organization_id,
            target.user_id,
            SessionManager.is_active(target.expires_at, target.revoked_at),
        ),
    )


def active_scope(user: User):
    return (
        Session.user_id == user.id,
        Session.organization_id == user.organization_id,
        Session.revoked_at.is_(None),
        Session.expires_at > datetime.now(timezone.utc),
    )


def timestamp(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


@router.get("")
async def inventory(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    cursor: UUID | None = None,
    db: AsyncSession = Depends(get_db_session),
):
    user, current = await resolve_owner(request, db)
    authorize_session(user, Action.SESSION_READ, current)
    query = select(Session.id, Session.created_at, Session.expires_at).where(*active_scope(user))
    if cursor is not None:
        query = query.where(Session.id > cursor)
    rows = (await db.execute(query.order_by(Session.id).limit(limit + 1))).all()
    visible = rows[:limit]
    envelope = success_response(
        request,
        [
            {
                "id": str(row.id),
                "createdAt": timestamp(row.created_at),
                "expiresAt": timestamp(row.expires_at),
                "current": row.id == current.id,
            }
            for row in visible
        ],
    )
    envelope["meta"]["pagination"] = {
        "nextCursor": str(visible[-1].id) if len(rows) > limit else None,
    }
    return envelope


@router.post("/revoke-others", dependencies=[Depends(trusted_origin)])
async def revoke_others(request: Request, db: AsyncSession = Depends(get_db_session)):
    user, current = await resolve_owner(request, db, lock=True)
    authorize_session(user, Action.SESSION_REVOKE, current)
    result = await db.execute(
        update(Session)
        .where(*active_scope(user), Session.id != current.id)
        .values(revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    count = result.rowcount
    await db.commit()
    return success_response(request, {"revokedCount": count})


@router.delete("/{session_id}", dependencies=[Depends(trusted_origin)])
async def revoke_one(
    session_id: UUID,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
):
    user, current = await resolve_owner(request, db, lock=True)
    authorize_session(user, Action.SESSION_REVOKE, current)
    target = await db.scalar(
        select(Session).where(*active_scope(user), Session.id == session_id)
    )
    if target is None:
        raise ApiError(404, "NOT_FOUND", "Active session was not found.")
    authorize_session(user, Action.SESSION_REVOKE, target)
    # Owner predicate is in SQL. Foreign, stale and nonexistent IDs share one response.
    result = await db.execute(
        update(Session)
        .where(*active_scope(user), Session.id == session_id)
        .values(revoked_at=datetime.now(timezone.utc))
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ApiError(404, "NOT_FOUND", "Active session was not found.")
    await db.commit()
    if session_id == current.id:
        settings = request.app.state.settings
        response.delete_cookie(
            settings.session_cookie_name,
            path="/",
            httponly=True,
            secure=settings.environment == "production",
            samesite="lax",
        )
    return success_response(request, {"success": True})
