"""Opaque document cursors bound to the tenant and list filter.

These cursors encode position, not authority. Every database query must still
apply its own tenant predicate.
"""

import base64
import binascii
import json
from datetime import datetime
from uuid import UUID

from app.core.errors import ApiError


def decode_document_cursor(
    cursor: str, *, organization_id: UUID, status: str | None
) -> tuple[datetime, UUID]:
    """Validate the existing wire format and return the keyset position."""
    try:
        decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
        if not isinstance(decoded, list) or len(decoded) != 4:
            raise ValueError()
        tenant, cursor_status, timestamp, identity = decoded
        received_at = datetime.fromisoformat(timestamp)
        document_id = UUID(identity)
        if tenant != str(organization_id) or cursor_status != status or received_at.tzinfo is None:
            raise ValueError()
    except (ValueError, TypeError, binascii.Error):
        raise ApiError(400, "INVALID_CURSOR", "Document cursor is invalid.") from None
    return received_at, document_id


def encode_document_cursor(
    *, organization_id: UUID, status: str | None, received_at: datetime, document_id: UUID
) -> str:
    """Preserve the compact base64 JSON format used by existing clients."""
    position = [str(organization_id), status, received_at.isoformat(), str(document_id)]
    return base64.urlsafe_b64encode(json.dumps(position, separators=(",", ":")).encode()).decode()
