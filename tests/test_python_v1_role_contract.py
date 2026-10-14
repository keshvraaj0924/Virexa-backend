from app.core.rbac import Role


def test_v1_roles_are_explicit():
    assert {role.value for role in Role} == {
        'super_admin', 'admin', 'manager', 'operator', 'viewer'
    }
