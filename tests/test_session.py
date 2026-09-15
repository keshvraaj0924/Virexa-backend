from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.core.session import SessionManager


def test_issue_returns_opaque_token_digest_and_expiry():
    manager = SessionManager(ttl_seconds=3600)
    token, digest, expires_at = manager.issue(uuid4(), uuid4())

    assert len(token) >= 48
    assert len(digest) == 64
    assert expires_at > datetime.now(timezone.utc)


def test_revoked_session_is_inactive():
    manager = SessionManager(ttl_seconds=3600)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    assert not manager.is_active(expires_at, datetime.now(timezone.utc))


def test_expired_session_is_inactive():
    manager = SessionManager(ttl_seconds=3600)
    expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    assert not manager.is_active(expires_at, None)
