from uuid import uuid4

import pytest

from app.core.tenant import TenantContext, require_same_tenant


def test_same_tenant_resource_is_allowed():
    organization_id = uuid4()
    context = TenantContext(organization_id, uuid4(), "operator")
    require_same_tenant(organization_id, context)


def test_cross_tenant_resource_is_rejected():
    context = TenantContext(uuid4(), uuid4(), "operator")
    with pytest.raises(PermissionError):
        require_same_tenant(uuid4(), context)
