"""Version-one frontend permission vocabulary, matching src/auth/context.ts.

This does not authorize Python endpoints: each endpoint enforces its own server
policy. Unported endpoints remain unavailable.
"""

WIRE_ROLE_PERMISSIONS: dict[str, list[str]] = {
    "super_admin": [
        "platform:read",
        "platform:manage",
        "organization:manage",
        "audit:read",
        "workflow:read",
        "workflow:create",
        "workflow:manage",
        "document:read",
        "document:create",
        "document:manage",
        "agent:read",
        "agent:create",
        "agent:manage",
    ],
    "admin": [
        "platform:read",
        "organization:manage",
        "audit:read",
        "workflow:read",
        "workflow:create",
        "workflow:manage",
        "document:read",
        "document:create",
        "document:manage",
        "agent:read",
        "agent:create",
        "agent:manage",
    ],
    "manager": [
        "platform:read",
        "audit:read",
        "workflow:read",
        "workflow:create",
        "workflow:manage",
        "document:read",
        "document:create",
        "document:manage",
        "agent:read",
        "agent:create",
        "agent:manage",
    ],
    "operator": [
        "platform:read",
        "workflow:read",
        "workflow:create",
        "document:read",
        "document:create",
        "agent:read",
        "agent:create",
    ],
    "viewer": ["platform:read", "workflow:read", "document:read", "agent:read"],
}
