from contextvars import ContextVar
from uuid import uuid4


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    value = str(uuid4())
    _request_id.set(value)
    return value


def get_request_id() -> str:
    value = _request_id.get()
    if value is None:
        return new_request_id()
    return value
