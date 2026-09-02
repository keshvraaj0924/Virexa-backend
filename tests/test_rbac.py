from app.core.rbac import Permission, Role, has_permission


def test_viewer_has_dashboard_access():
    assert has_permission(Role.VIEWER, Permission.DASHBOARD_READ)


def test_viewer_cannot_manage_users():
    assert not has_permission(Role.VIEWER, Permission.USER_MANAGE)


def test_auditor_is_limited_to_audit_surface():
    assert has_permission(Role.AUDITOR, Permission.AUDIT_READ)
    assert not has_permission(Role.AUDITOR, Permission.WORKFLOW_MANAGE)
