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
