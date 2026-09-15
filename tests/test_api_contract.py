from unittest.mock import Mock

from app.core.api import error_response, success_response


def test_success_response_contains_stable_metadata():
    request = Mock()
    request.state.request_id = "req-123"

    payload = success_response(request, {"ok": True})

    assert payload["data"] == {"ok": True}
    assert payload["meta"]["requestId"] == "req-123"
    assert "timestamp" in payload["meta"]


def test_error_response_keeps_field_errors_machine_readable():
    request = Mock()
    request.state.request_id = "req-456"

    payload = error_response(request, "VALIDATION_ERROR", "Invalid request", {"email": ["Invalid email"]})

    assert payload["error"]["code"] == "VALIDATION_ERROR"
    assert payload["error"]["fieldErrors"] == {"email": ["Invalid email"]}
    assert payload["meta"]["requestId"] == "req-456"
