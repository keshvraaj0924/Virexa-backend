from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Request


def request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid4()))


def response_meta(request: Request) -> dict[str, str]:
    return {
        "requestId": request_id(request),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def success_response(request: Request, data: object) -> dict[str, object]:
    return {"data": data, "meta": response_meta(request)}


def error_response(
    request: Request,
    code: str,
    message: str,
    field_errors: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    error: dict[str, object] = {
        "code": code,
        "message": message,
        "requestId": request_id(request),
    }
    if field_errors:
        error["fieldErrors"] = field_errors
    return {"error": error, "meta": response_meta(request)}
