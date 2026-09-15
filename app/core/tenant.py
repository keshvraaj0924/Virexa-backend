from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Trusted tenant boundary derived from authenticated server-side state."""

    organization_id: UUID
    user_id: UUID
    role: str


def require_same_tenant(resource_organization_id: UUID, context: TenantContext) -> None:
    """Reject access whenever a resource crosses the authenticated tenant boundary."""
    if resource_organization_id != context.organization_id:
        raise PermissionError("Resource does not belong to the authenticated organization")
