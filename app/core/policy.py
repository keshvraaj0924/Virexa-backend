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

    @property
    def account_type(self) -> str: ...


class Action(StrEnum):
    MEMBERS_READ = "organization:members:read"
    MEMBERS_MANAGE = "organization:members:manage"
    HIERARCHY_READ = "organization:hierarchy:read"
    WORKFLOW_READ = "workflow:read"
    WORKFLOW_CREATE = "workflow:create"
    WORKFLOW_MANAGE = "workflow:manage"
    AUDIT_READ = "audit:read"
    USAGE_READ = "usage:read"
    USAGE_ORGANIZATION_READ = "usage:organization:read"
    LAYOUT_REQUEST = "document:layout:request"
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
class WorkflowAttributes:
    organization_id: UUID
    owner_id: UUID
    status: str


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
    if action in {Action.MEMBERS_READ, Action.MEMBERS_MANAGE, Action.HIERARCHY_READ}:
        if getattr(subject, "account_type", None) != "organization":
            return False
        allowed = {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN}
        if action != Action.MEMBERS_MANAGE:
            allowed.add(Role.AUDITOR)
        return subject.role in allowed
    if action == Action.WORKFLOW_MANAGE:
        return subject.role in {
            Role.PLATFORM_ADMIN,
            Role.TENANT_ADMIN,
            Role.ORGANIZATION_ADMIN,
            Role.MANAGER,
        }
    if action in {Action.AUDIT_READ, Action.USAGE_ORGANIZATION_READ}:
        return subject.role in {
            Role.PLATFORM_ADMIN,
            Role.TENANT_ADMIN,
            Role.ORGANIZATION_ADMIN,
            Role.AUDITOR,
        }
    if action in {Action.USAGE_READ, Action.SESSION_READ, Action.SESSION_REVOKE}:
        return True
    required = {
        Action.WORKFLOW_READ: Permission.WORKFLOW_READ,
        Action.WORKFLOW_CREATE: Permission.WORKFLOW_MANAGE,
        Action.DOCUMENT_READ: Permission.DOCUMENT_READ,
        Action.DOCUMENT_CREATE: Permission.DOCUMENT_MANAGE,
        Action.DOCUMENT_UPLOAD: Permission.DOCUMENT_MANAGE,
        Action.ORGANIZATION_READ: Permission.USER_READ,
        Action.EXTRACTION_REQUEST: Permission.DOCUMENT_MANAGE,
        Action.LAYOUT_REQUEST: Permission.DOCUMENT_MANAGE,
    }.get(action)
    return bool(
        required
        and has_permission(subject.role, required)
        and not (action in {Action.EXTRACTION_REQUEST, Action.LAYOUT_REQUEST} and subject.role == Role.OPERATOR)
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
    if action in {Action.EXTRACTION_REQUEST, Action.LAYOUT_REQUEST}:
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


def require_workflow(
    subject: Subject, action: Action, resource: WorkflowAttributes, next_status: str | None = None
) -> None:
    require_action(subject, action)
    if resource.organization_id != subject.organization_id:
        raise PolicyDenied(404, "NOT_FOUND", "Workflow was not found.")
    if action == Action.WORKFLOW_READ:
        return
    if action not in {Action.WORKFLOW_CREATE, Action.WORKFLOW_MANAGE}:
        raise PolicyDenied(403, "FORBIDDEN", "Insufficient permissions.")
    if not role_allows(subject, Action.WORKFLOW_MANAGE) and resource.owner_id != subject.user_id:
        raise PolicyDenied(403, "FORBIDDEN", "You cannot modify this workflow.")
    transitions = {
        "draft": {"active"},
        "active": {"paused", "archived"},
        "paused": {"active", "archived"},
        "archived": set(),
    }
    if (
        next_status
        and next_status != resource.status
        and next_status not in transitions.get(resource.status, set())
    ):
        raise PolicyDenied(
            409,
            "INVALID_STATE_TRANSITION",
            f"Workflow cannot transition from {resource.status} to {next_status}.",
        )
