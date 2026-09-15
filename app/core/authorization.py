from dataclasses import dataclass
from uuid import UUID

from app.core.rbac import Permission, Role, has_permission


@dataclass(frozen=True, slots=True)
class Principal:
    """Authenticated identity and tenant scope used by authorization checks."""

    user_id: UUID
    tenant_id: UUID
    organization_id: UUID
    role: Role


def authorize(principal: Principal, permission: Permission, resource_tenant_id: UUID) -> None:
    """Reject access unless both permission and tenant boundaries are satisfied."""
    if principal.tenant_id != resource_tenant_id:
        raise PermissionError("Resource is outside the authenticated tenant")
    if not has_permission(principal.role, permission):
        raise PermissionError(f"Missing permission: {permission}")
