from uuid import uuid4

import pytest

from app.core.auth_context import AuthContext
from app.core.policy import (
    Action,
    DocumentAttributes,
    PolicyDenied,
    SessionAttributes,
    capabilities,
    require_action,
    require_document,
    require_session,
)
from app.core.rbac import Role


@pytest.mark.parametrize("role", list(Role))
def test_capability_matrix_matches_decisions(role):
    subject = AuthContext(uuid4(), uuid4(), role)
    expected = {"session:read", "session:revoke", "usage:read"}
    if role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.AUDITOR}:
        expected |= {"usage:organization:read", "audit:read"}
    if role != Role.AUDITOR:
        expected.add("document:read")
    if role not in {Role.AUDITOR, Role.VIEWER}:
        expected |= {"document:create", "document:upload"}
    if role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.MANAGER}:
        expected.add("organization:read")
    assert capabilities(subject) == sorted(expected)
    for action in Action:
        allowed = action.value in expected or (
            action == Action.EXTRACTION_REQUEST
            and role
            in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN, Role.MANAGER}
        )
        if allowed:
            require_action(subject, action)
        else:
            with pytest.raises(PolicyDenied) as error:
                require_action(subject, action)
            assert error.value.status == 403


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(
    "same_owner,same_tenant,active",
    [
        (True, True, True),
        (False, True, True),
        (True, False, True),
        (True, True, False),
    ],
)
def test_session_abac_has_no_admin_bypass(role, same_owner, same_tenant, active):
    subject = AuthContext(uuid4(), uuid4(), role)
    target = SessionAttributes(
        subject.organization_id if same_tenant else uuid4(),
        subject.user_id if same_owner else uuid4(),
        active,
    )
    for action in (Action.SESSION_READ, Action.SESSION_REVOKE):
        if same_owner and same_tenant and active:
            require_session(subject, action, target)
        else:
            with pytest.raises(PolicyDenied) as error:
                require_session(subject, action, target)
            assert error.value.status == 404


@pytest.mark.parametrize(
    "state", ["received", "processing", "review_required", "completed", "failed"]
)
@pytest.mark.parametrize("scan", ["awaiting_upload", "pending_scan", "clean", "rejected"])
def test_upload_resource_state_matrix(state, scan):
    subject = AuthContext(uuid4(), uuid4(), Role.OPERATOR)
    target = DocumentAttributes(subject.organization_id, state, scan)
    require_document(subject, Action.DOCUMENT_READ, target)
    if state == "received" and scan == "awaiting_upload":
        require_document(subject, Action.DOCUMENT_UPLOAD, target)
    else:
        with pytest.raises(PolicyDenied) as error:
            require_document(subject, Action.DOCUMENT_UPLOAD, target)
        assert error.value.code == "DOCUMENT_NOT_WRITABLE"


def test_unknown_roles_actions_and_cross_tenant_fail_closed():
    subject = AuthContext(uuid4(), uuid4(), Role.PLATFORM_ADMIN)
    with pytest.raises(PolicyDenied) as error:
        require_document(
            subject,
            Action.DOCUMENT_UPLOAD,
            DocumentAttributes(uuid4(), "received", "awaiting_upload"),
        )
    assert error.value.status == 404
    with pytest.raises(PolicyDenied):
        require_action(subject, "not:a:capability")
    invalid = AuthContext(subject.user_id, subject.organization_id, "invented-admin")
    assert capabilities(invalid) == []
    with pytest.raises(PolicyDenied):
        require_action(invalid, Action.SESSION_READ)
