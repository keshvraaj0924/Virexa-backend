from datetime import datetime, timezone
from uuid import UUID

from app.core.security import generate_session_token, session_expiry, session_token_digest


class SessionManager:
    """Application service for issuing and validating opaque server-side sessions.

    Persistence is deliberately injected so this security primitive does not depend
    on a concrete database implementation.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self._ttl_seconds = ttl_seconds

    def issue(self, user_id: UUID, organization_id: UUID) -> tuple[str, str, datetime]:
        token = generate_session_token()
        return token, session_token_digest(token), session_expiry(self._ttl_seconds)

    @staticmethod
    def is_active(expires_at: datetime, revoked_at: datetime | None) -> bool:
        now = datetime.now(timezone.utc)
        normalized_expiry = expires_at if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        return revoked_at is None and normalized_expiry > now
