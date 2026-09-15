from uuid import uuid4

import pytest

from app.core.authorization import Principal, authorize
from app.core.rbac import Permission, Role


def principal() -> Principal:
    tenant_id = uuid4()
    return Principal(uuid4(), tenant_id, uuid4(), Role.MANAGER)


def test_authorize_allows_permission_inside_tenant():
    subject = principal()
    authorize(subject, Permission.WORKFLOW_READ, subject.tenant_id)


def test_authorize_rejects_cross_tenant_resource():
    subject = principal()
    with pytest.raises(PermissionError, match="outside"):
        authorize(subject, Permission.WORKFLOW_READ, uuid4())


def test_authorize_rejects_missing_permission():
    subject = Principal(uuid4(), uuid4(), uuid4(), Role.VIEWER)
    with pytest.raises(PermissionError, match="Missing permission"):
        authorize(subject, Permission.WORKFLOW_MANAGE, subject.tenant_id)
