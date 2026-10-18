from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authentication import AuthenticationService
from app.core.policy import Action, require_action
from app.core.rbac import Permission, Role, has_permission
from app.db.base import get_db_session
from app.db.models import Organization


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    organization_id: UUID
    role: Role
    account_type: str = "organization"
    session_id: UUID | None = None


_authentication = AuthenticationService()


async def get_auth_context(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    session_token = request.cookies.get(request.app.state.settings.session_cookie_name)
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )

    resolved = await _authentication.resolve_session(db, session_token)
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session"
        )

    user, session = resolved
    try:
        role = Role(user.role)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid account role"
        ) from exc

    organization = await db.get(Organization, user.organization_id)
    if organization is None:
        raise HTTPException(status_code=403, detail="Invalid workspace")
    context = AuthContext(
        user_id=user.id,
        organization_id=user.organization_id,
        role=role,
        account_type=organization.account_type,
        session_id=session.id,
    )
    request.state.audit_subject = context
    return context


def require_permission(permission: Permission):
    async def dependency(context: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not has_permission(context.role, permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions"
            )
        return context

    return dependency


def require_capability(action: Action) -> Callable[..., Awaitable[AuthContext]]:
    async def dependency(context: AuthContext = Depends(get_auth_context)) -> AuthContext:
        require_action(context, action)
        return context

    return dependency
