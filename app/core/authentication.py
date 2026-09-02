from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, session_token_digest, verify_password
from app.db.models import Session, User


class AuthenticationService:
    """Database-backed authentication and session lifecycle service."""

    async def authenticate(self, db: AsyncSession, email: str, password: str) -> User | None:
        result = await db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()
        if user is None or not verify_password(password, user.password_hash):
            return None
        return user

    async def resolve_session(self, db: AsyncSession, token: str) -> tuple[User, Session] | None:
        result = await db.execute(
            select(Session, User)
            .join(User, User.id == Session.user_id)
            .where(Session.token_digest == session_token_digest(token))
        )
        row = result.one_or_none()
        if row is None:
            return None
        session, user = row
        now = datetime.now(timezone.utc)
        expires_at = session.expires_at
        if session.revoked_at is not None or (expires_at.tzinfo is None and expires_at.replace(tzinfo=timezone.utc) <= now) or (expires_at.tzinfo is not None and expires_at <= now):
            return None
        return user, session

    async def revoke(self, db: AsyncSession, token: str) -> bool:
        result = await db.execute(
            update(Session)
            .where(Session.token_digest == session_token_digest(token), Session.revoked_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )
        await db.commit()
        return result.rowcount == 1

    @staticmethod
    def hash_password(password: str) -> str:
        return hash_password(password)
