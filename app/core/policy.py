"""Deny-by-default policy for implemented Python operations.

Subjects and resource attributes must come from authenticated database rows,
never request bodies, headers, or the legacy wire-role permission catalogue.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.errors import ApiError
from app.core.rbac import Permission, Role, has_permission


class Subject(Protocol):
    @property
    def user_id(self) -> UUID: ...

    @property
    def organization_id(self) -> UUID: ...

    @property
    def role(self) -> Role: ...


class Action(StrEnum):
    DOCUMENT_READ = "document:read"
    DOCUMENT_CREATE = "document:create"
    DOCUMENT_UPLOAD = "document:upload"
    SESSION_READ = "session:read"
    SESSION_REVOKE = "session:revoke"
    ORGANIZATION_READ = "organization:read"
    # Policy exists for the guarded unavailable route, not an advertised capability.
    EXTRACTION_REQUEST = "extraction:request"


@dataclass(frozen=True)
class DocumentAttributes:
    organization_id: UUID
    status: str
    scan_status: str


@dataclass(frozen=True)
class SessionAttributes:
    organization_id: UUID
    owner_id: UUID
    active: bool


class PolicyDenied(ApiError):
    """Public, non-enumerating denial with the normal API error envelope."""


def role_allows(subject: Subject, action: Action) -> bool:
    if subject.role not in set(Role):
        return False
    if action in {Action.SESSION_READ, Action.SESSION_REVOKE}:
        return True
    required = {
        Action.DOCUMENT_READ: Permission.DOCUMENT_READ,
        Action.DOCUMENT_CREATE: Permission.DOCUMENT_MANAGE,
        Action.DOCUMENT_UPLOAD: Permission.DOCUMENT_MANAGE,
        Action.ORGANIZATION_READ: Permission.USER_READ,
        Action.EXTRACTION_REQUEST: Permission.DOCUMENT_MANAGE,
    }.get(action)
    return bool(
        required
        and has_permission(subject.role, required)
        and not (action == Action.EXTRACTION_REQUEST and subject.role == Role.OPERATOR)
    )


def capabilities(subject: Subject) -> list[str]:
    """Collection capabilities only; resource policy must still run on each operation."""
    return sorted(
        action.value
        for action in Action
        if action != Action.EXTRACTION_REQUEST and role_allows(subject, action)
    )


def require_action(subject: Subject, action: Action) -> None:
    if not role_allows(subject, action):
        raise PolicyDenied(403, "FORBIDDEN", "Insufficient permissions.")


def require_document(subject: Subject, action: Action, resource: DocumentAttributes) -> None:
    require_action(subject, action)
    if resource.organization_id != subject.organization_id:
        raise PolicyDenied(404, "NOT_FOUND", "Document was not found.")
    if action == Action.DOCUMENT_READ:
        return
    if action == Action.DOCUMENT_UPLOAD:
        if resource.status == "received" and resource.scan_status == "awaiting_upload":
            return
        raise PolicyDenied(409, "DOCUMENT_NOT_WRITABLE", "Document is no longer accepting uploads.")
    if action == Action.EXTRACTION_REQUEST:
        if resource.scan_status == "clean":
            return
        raise PolicyDenied(
            409, "DOCUMENT_NOT_READY", "A verified upload and clean malware scan are required."
        )
    raise PolicyDenied(403, "FORBIDDEN", "Insufficient permissions.")


def require_session(subject: Subject, action: Action, resource: SessionAttributes) -> None:
    require_action(subject, action)
    if action not in {Action.SESSION_READ, Action.SESSION_REVOKE}:
        raise PolicyDenied(403, "FORBIDDEN", "Insufficient permissions.")
    if (
        resource.organization_id != subject.organization_id
        or resource.owner_id != subject.user_id
        or not resource.active
    ):
        raise PolicyDenied(404, "NOT_FOUND", "Active session was not found.")
