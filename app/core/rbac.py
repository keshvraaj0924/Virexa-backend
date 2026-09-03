from enum import StrEnum


class Role(StrEnum):
    PLATFORM_ADMIN = "platform_admin"
    TENANT_ADMIN = "tenant_admin"
    ORGANIZATION_ADMIN = "organization_admin"
    MANAGER = "manager"
    OPERATOR = "operator"
    VIEWER = "viewer"
    AUDITOR = "auditor"


class Permission(StrEnum):
    DASHBOARD_READ = "dashboard:read"
    USER_READ = "user:read"
    USER_MANAGE = "user:manage"
    ROLE_MANAGE = "role:manage"
    WORKFLOW_READ = "workflow:read"
    WORKFLOW_MANAGE = "workflow:manage"
    DOCUMENT_READ = "document:read"
    DOCUMENT_MANAGE = "document:manage"
    AUDIT_READ = "audit:read"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.PLATFORM_ADMIN: frozenset(Permission),
    Role.TENANT_ADMIN: frozenset({p for p in Permission if p != Permission.AUDIT_READ}) | {Permission.AUDIT_READ},
    Role.ORGANIZATION_ADMIN: frozenset({Permission.DASHBOARD_READ, Permission.USER_READ, Permission.USER_MANAGE, Permission.ROLE_MANAGE, Permission.WORKFLOW_READ, Permission.WORKFLOW_MANAGE, Permission.DOCUMENT_READ, Permission.DOCUMENT_MANAGE}),
    Role.MANAGER: frozenset({Permission.DASHBOARD_READ, Permission.USER_READ, Permission.WORKFLOW_READ, Permission.WORKFLOW_MANAGE, Permission.DOCUMENT_READ, Permission.DOCUMENT_MANAGE}),
    Role.OPERATOR: frozenset({Permission.DASHBOARD_READ, Permission.WORKFLOW_READ, Permission.WORKFLOW_MANAGE, Permission.DOCUMENT_READ, Permission.DOCUMENT_MANAGE}),
    Role.VIEWER: frozenset({Permission.DASHBOARD_READ, Permission.WORKFLOW_READ, Permission.DOCUMENT_READ}),
    Role.AUDITOR: frozenset({Permission.DASHBOARD_READ, Permission.AUDIT_READ}),
}


def has_permission(role: Role, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())
