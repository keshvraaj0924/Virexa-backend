import base64
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.errors import ApiError
from app.domain.documents.pagination import decode_document_cursor, encode_document_cursor

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
DOCUMENT_ID = UUID("22222222-2222-2222-2222-222222222222")
RECEIVED_AT = datetime(2026, 10, 17, tzinfo=timezone.utc)


def wire_cursor(position):
    return base64.urlsafe_b64encode(json.dumps(position, separators=(",", ":")).encode()).decode()


@pytest.mark.parametrize("status", [None, "uploaded"])
def test_cursor_preserves_wire_format_and_round_trips(status):
    cursor = encode_document_cursor(
        organization_id=TENANT_ID,
        status=status,
        received_at=RECEIVED_AT,
        document_id=DOCUMENT_ID,
    )
    assert cursor == wire_cursor(
        [str(TENANT_ID), status, RECEIVED_AT.isoformat(), str(DOCUMENT_ID)]
    )
    assert decode_document_cursor(cursor, organization_id=TENANT_ID, status=status) == (
        RECEIVED_AT,
        DOCUMENT_ID,
    )


@pytest.mark.parametrize(
    "position",
    [
        {},
        [],
        [str(TENANT_ID)],
        [str(uuid4()), None, RECEIVED_AT.isoformat(), str(DOCUMENT_ID)],
        [str(TENANT_ID), "uploaded", RECEIVED_AT.isoformat(), str(DOCUMENT_ID)],
        [str(TENANT_ID), None, "2026-10-17T00:00:00", str(DOCUMENT_ID)],
        [str(TENANT_ID), None, "invalid-date", str(DOCUMENT_ID)],
        [str(TENANT_ID), None, None, str(DOCUMENT_ID)],
        [str(TENANT_ID), None, RECEIVED_AT.isoformat(), "invalid-uuid"],
    ],
)
def test_cursor_rejects_invalid_shape_scope_filter_or_position(position):
    with pytest.raises(ApiError) as caught:
        decode_document_cursor(wire_cursor(position), organization_id=TENANT_ID, status=None)
    assert caught.value.status == 400
    assert caught.value.code == "INVALID_CURSOR"


@pytest.mark.parametrize("cursor", ["not base64!", "e30=", "////"])
def test_cursor_returns_safe_error_for_invalid_encoding(cursor):
    with pytest.raises(ApiError) as caught:
        decode_document_cursor(cursor, organization_id=TENANT_ID, status=None)
    assert caught.value.code == "INVALID_CURSOR"
