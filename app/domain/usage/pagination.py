"""Opaque keyset positions, bound to scope and normalized query filters."""

import base64
import binascii
import json
from datetime import datetime, timezone
from uuid import UUID

from app.core.errors import ApiError


def encode(scope, row):
    at = row.created_at
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return base64.urlsafe_b64encode(
        json.dumps([scope, at.isoformat(), str(row.id)], separators=(",", ":")).encode()
    ).decode()


def decode(cursor, scope):
    try:
        data = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(data, list) or len(data) != 3 or data[0] != scope:
            raise ValueError()
        if not isinstance(data[1], str) or not isinstance(data[2], str):
            raise ValueError()
        at, identity = datetime.fromisoformat(data[1]), UUID(data[2])
        if at.tzinfo is None:
            raise ValueError()
        return at, identity
    except (ValueError, TypeError, binascii.Error):
        raise ApiError(400, "INVALID_CURSOR", "Usage cursor is invalid.") from None
