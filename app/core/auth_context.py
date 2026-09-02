from dataclasses import dataclass
from uuid import UUID

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authentication import AuthenticationService
from app.core.rbac import Permission, Role, has_permission
from app.db.base import get_db_session
from app.db.models import User
from app.core.config import get_settings


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    organization_id: UUID
    role: Role


_authentication = AuthenticationService()


async def get_auth_context(
    session_token: str | None = Cookie(default=None, alias=get_settings().session_cookie_name),
    db: AsyncSession = Depends(get_db_session),
) -> AuthContext:
    if not session_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    resolved = await _authentication.resolve_session(db, session_token)
    if resolved is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired session")

    user: User
    _, user = resolved
    try:
        role = Role(user.role)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid account role") from exc

    return AuthContext(user_id=user.id, organization_id=user.organization_id, role=role)


def require_permission(permission: Permission):
    async def dependency(context: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not has_permission(context.role, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
        return context

    return dependency
