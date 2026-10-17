from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.core.security import hash_password, session_token_digest, verify_password
from app.core.session import SessionManager
from app.db.models import Session, User

# Equal-cost password verification for absent or ambiguous identities.
_DUMMY_HASH = hash_password("unused-random-dummy-verification-value")


class AuthenticationService:
    """Database-backed authentication and session lifecycle service."""

    async def authenticate(self, db: AsyncSession, email: str, password: str) -> User | None:
        result = await db.execute(
            select(User).where(func.lower(User.email) == email.lower()).limit(2)
        )
        users = list(result.scalars())
        user = users[0] if len(users) == 1 else None
        valid = await run_in_threadpool(
            verify_password, password, user.password_hash if user else _DUMMY_HASH
        )
        return user if valid else None

    async def resolve_session(self, db: AsyncSession, token: str) -> tuple[User, Session] | None:
        if len(token) > 256:
            return None
        result = await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(
                Session.token_digest == session_token_digest(token),
                Session.organization_id == User.organization_id,
            )
            .execution_options(populate_existing=True)
        )
        row = result.one_or_none()
        if row is None:
            return None
        session, user = row
        if not SessionManager.is_active(session.expires_at, session.revoked_at):
            return None
        return user, session

    async def revoke(self, db: AsyncSession, token: str) -> bool:
        owner_id = await db.scalar(
            select(Session.user_id).where(Session.token_digest == session_token_digest(token))
        )
        if owner_id is None:
            return False
        await db.execute(select(User.id).where(User.id == owner_id).with_for_update())
        result = await db.execute(
            update(Session)
            .where(
                Session.token_digest == session_token_digest(token), Session.revoked_at.is_(None)
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()
        return result.rowcount == 1

    @staticmethod
    def hash_password(password: str) -> str:
        return hash_password(password)
