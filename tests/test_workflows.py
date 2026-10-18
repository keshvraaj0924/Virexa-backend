# ruff: noqa: F811
"""Real HTTP/database contract tests; concurrency cases run on PostgreSQL 16."""

import asyncio
import base64
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from test_document_intake import ORIGIN, app, client, signup  # noqa: F401

from app.core.rbac import Role
from app.db.models import User
from app.domain.audit.models import AuditEvent
from app.domain.usage.models import UsageEvent
from app.domain.workflows.models import Workflow, WorkflowKey

BASE = "/api/v1/workflows"


async def create(c, key=None, **fields):
    return await c.post(
        BASE,
        headers={**ORIGIN, "Idempotency-Key": key or str(uuid4())},
        json={"name": "Invoice approval", **fields},
    )


async def patch(c, row, **fields):
    return await c.patch(
        f"{BASE}/{row['id']}", headers=ORIGIN, json={"expectedVersion": row["version"], **fields}
    )


async def test_wire_contract_replay_lifecycle_audit_and_no_execution(app):
    async with client(app) as c:
        who = await signup(c)
        key = str(uuid4())
        response = await create(c, key, name="  Tracking only  ", description="  ")
        assert response.status_code == 201, response.text
        row = response.json()["data"]
        assert set(row) == {
            "id",
            "organizationId",
            "createdByUserId",
            "name",
            "description",
            "status",
            "version",
            "createdAt",
            "updatedAt",
        }
        assert row["createdAt"].endswith("Z") and row["updatedAt"].endswith("Z")
        assert row["createdByUserId"] == who["id"]
        assert (row["name"], row["description"], row["status"], row["version"]) == (
            "Tracking only",
            None,
            "draft",
            1,
        )
        replay = await create(c, key, name="Tracking only")
        assert replay.status_code == 200
        assert replay.json()["data"] == row
        conflict = await create(c, key, name="Different")
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
        invalid = await patch(c, row, status="archived")
        assert invalid.status_code == 409
        assert invalid.json()["error"]["code"] == "INVALID_STATE_TRANSITION"
        for status in ["active", "active", "paused", "active", "archived"]:
            changed = await patch(c, row, status=status)
            assert changed.status_code == 200, changed.text
            newer = changed.json()["data"]
            assert newer["version"] == row["version"] + 1
            stale = await patch(c, row, status="draft")
            assert stale.json()["error"]["code"] == "WORKFLOW_CONFLICT"
            row = newer
        assert (await patch(c, row, status="active")).status_code == 409
        cleared = await patch(c, row, description="   ")
        assert cleared.status_code == 200
        assert cleared.json()["data"]["description"] is None
        for endpoint in ["run", "execute", "integrations"]:
            assert (
                await c.post(f"{BASE}/{row['id']}/{endpoint}", headers=ORIGIN)
            ).status_code == 404
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(UsageEvent)) == 0
            actions = list(
                await db.scalars(
                    select(AuditEvent.action).where(AuditEvent.resource_type == "workflow")
                )
            )
            assert actions.count("workflow.created") == 1
            assert actions.count("workflow.updated") == 6
            assert "Tracking only" not in str(list(await db.scalars(select(AuditEvent.details))))


@pytest.mark.parametrize("role", list(Role))
async def test_trusted_role_owner_and_tenant_policy(app, role):
    async with client(app) as c, client(app) as other:
        who = await signup(c)
        foreign_user = await signup(other, "foreign@example.com")
        own = (await create(c)).json()["data"]
        foreign = (await create(other)).json()["data"]
        async with app.state.session_factory() as db:
            coworker = User(
                id=uuid4(),
                organization_id=UUID(who["organizationId"]),
                display_name="Other",
                email="coworker@example.com",
                password_hash="unused",
                role="operator",
            )
            db.add(coworker)
            await db.flush()
            await db.execute(
                update(Workflow)
                .where(Workflow.id == UUID(own["id"]))
                .values(created_by_user_id=coworker.id)
            )
            await db.execute(update(User).where(User.id == UUID(who["id"])).values(role=role.value))
            await db.commit()
        readable = role != Role.AUDITOR
        writable = role not in {Role.AUDITOR, Role.VIEWER}
        manager = role in {
            Role.PLATFORM_ADMIN,
            Role.TENANT_ADMIN,
            Role.ORGANIZATION_ADMIN,
            Role.MANAGER,
        }
        assert (await c.get(BASE)).status_code == (200 if readable else 403)
        assert (await c.get(f"{BASE}/{own['id']}")).status_code == (200 if readable else 403)
        assert (await create(c)).status_code == (201 if writable else 403)
        assert (await patch(c, own, name="Coworker edit")).status_code == (200 if manager else 403)
        if writable:
            created = (await create(c)).json()["data"]
            assert (await patch(c, created, name="Own edit")).status_code == 200
        for identity in [foreign["id"], str(uuid4())]:
            response = await c.get(
                f"{BASE}/{identity}",
                headers={
                    "X-Role": "platform_admin",
                    "X-Organization-ID": foreign_user["organizationId"],
                },
            )
            assert response.status_code == (404 if readable else 403)
            assert (
                await c.patch(
                    f"{BASE}/{identity}", headers=ORIGIN, json={"expectedVersion": 1, "name": "No"}
                )
            ).status_code == (404 if writable else 403)
        if readable:
            assert foreign["id"] not in str((await c.get(BASE)).json())


async def test_validation_origin_and_bounded_metadata(app):
    async with client(app) as c:
        assert (await c.get(BASE)).status_code == 401
        await signup(c)
        for fields in [
            {"name": ""},
            {"name": "x" * 121},
            {"name": "😀" * 61},
            {"name": "bad\x00name"},
            {"description": "😀" * 501},
            {"description": "x" * 1001},
            {"status": "active"},
            {"organizationId": str(uuid4())},
            {"createdByUserId": str(uuid4())},
            {"steps": [{"action": "eval", "code": "anything"}]},
        ]:
            assert (await create(c, **fields)).status_code == 400
        invalid_unicode = await c.post(
            BASE,
            headers={**ORIGIN, "Content-Type": "application/json", "Idempotency-Key": str(uuid4())},
            content=b'{"name":"\\ud800"}',
        )
        assert invalid_unicode.status_code == 400
        for key in ["", "short", "x" * 256]:
            response = await c.post(
                BASE, headers={**ORIGIN, "Idempotency-Key": key}, json={"name": "Name"}
            )
            assert response.status_code == 400
        assert (await c.post(BASE, headers=ORIGIN, json={"name": "Name"})).status_code == 400
        assert (await c.post(BASE, json={"name": "Name"})).status_code == 403
        row = (await create(c)).json()["data"]
        for data in [
            {},
            {"expectedVersion": 1},
            {"expectedVersion": True, "name": "Name"},
            {"expectedVersion": 0, "name": "Name"},
            {"expectedVersion": 1, "name": None},
            {"expectedVersion": 1, "status": None},
            {"expectedVersion": 1, "status": "executed"},
            {"expectedVersion": 1, "version": 7},
        ]:
            assert (
                await c.patch(f"{BASE}/{row['id']}", headers=ORIGIN, json=data)
            ).status_code == 400
        response = await c.post(
            BASE,
            headers={**ORIGIN, "Content-Type": "application/json"},
            content=b"{" + b" " * 32768,
        )
        assert response.status_code == 413
        for params in [
            {"limit": 0},
            {"limit": 101},
            {"organizationId": str(uuid4())},
            {"status": "executed"},
            {"cursor": ""},
        ]:
            assert (await c.get(BASE, params=params)).status_code == 400
        for value in [
            None,
            [],
            {},
            {"createdAt": [], "id": []},
            {"createdAt": "2026-01-01", "id": str(uuid4())},
        ]:
            cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
            response = await c.get(BASE, params={"cursor": cursor})
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "INVALID_CURSOR"
        assert (await c.get(BASE + "/invalid")).status_code == 400
        assert (
            await c.options(
                BASE + "/" + row["id"], headers={**ORIGIN, "Access-Control-Request-Method": "PATCH"}
            )
        ).status_code == 200


async def test_stable_pagination_filters_and_tenant_key_scope(app):
    async with client(app) as c, client(app) as other:
        await signup(c)
        await signup(other, "other@example.com")
        key = str(uuid4())
        first = (await create(c, key)).json()["data"]
        foreign = (await create(other, key)).json()["data"]
        assert first["id"] != foreign["id"]
        for _ in range(4):
            await create(c)
        seen, cursor = [], None
        while True:
            result = await c.get(
                BASE, params={"limit": 2, **({"cursor": cursor} if cursor else {})}
            )
            assert "no-store" in result.headers["cache-control"]
            page = result.json()["data"]
            seen.extend(row["id"] for row in page["items"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        assert len(seen) == len(set(seen)) == 5
        assert foreign["id"] not in seen
        await patch(c, first, status="active")
        assert [
            row["id"]
            for row in (await c.get(BASE, params={"status": "active"})).json()["data"]["items"]
        ] == [first["id"]]


async def test_postgres_racing_create_patch_and_transaction_rollback(app, monkeypatch):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL concurrency is authoritative")
    async with client(app) as c:
        await signup(c)
        key = str(uuid4())
        results = await asyncio.gather(*[create(c, key) for _ in range(6)])
        assert sorted(r.status_code for r in results) == [200] * 5 + [201]
        row = results[0].json()["data"]
        assert len({r.json()["data"]["id"] for r in results}) == 1
        results = await asyncio.gather(*[patch(c, row, name=f"Racer {i}") for i in range(6)])
        assert sorted(r.status_code for r in results) == [200] + [409] * 5
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Workflow)) == 1
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.resource_type == "workflow")
                )
                == 2
            )

        async def unavailable(*args, **kwargs):
            raise SQLAlchemyError("Synthetic audit outage")

        monkeypatch.setattr("app.domain.workflows.service.record", unavailable)
        assert (await create(c)).status_code == 503
        current = (await c.get(f"{BASE}/{row['id']}")).json()["data"]
        assert (await patch(c, current, name="Must roll back")).status_code == 503
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Workflow)) == 1
            assert await db.scalar(select(func.count()).select_from(WorkflowKey)) == 1
        assert (await c.get(f"{BASE}/{row['id']}")).json()["data"] == current


async def test_personal_workspace_and_idempotency_after_edit(app):
    from app.db.models import Organization

    async with client(app) as c:
        identity = await signup(c, "personal@gmail.com")
        async with app.state.session_factory() as db:
            await db.execute(
                update(Organization)
                .where(Organization.id == UUID(identity["organizationId"]))
                .values(account_type="individual")
            )
            await db.commit()
        key = str(uuid4())
        original = (await create(c, key)).json()["data"]
        changed = (await patch(c, original, name="Updated personal record")).json()["data"]
        replay = await create(c, key)
        assert replay.status_code == 200
        assert replay.json()["data"] == changed
        assert (await c.get(BASE)).json()["data"]["items"] == [changed]


async def test_postgres_conflicting_idempotency_and_tenant_foreign_keys(app):
    from sqlalchemy.exc import IntegrityError

    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL constraints and races are authoritative")
    async with client(app) as c, client(app) as other:
        who = await signup(c)
        foreign = await signup(other, "foreign@example.com")
        key = str(uuid4())
        results = await asyncio.gather(create(c, key, name="A"), create(c, key, name="B"))
        assert sorted(r.status_code for r in results) == [201, 409]
        assert (
            next(r for r in results if r.status_code == 409).json()["error"]["code"]
            == "IDEMPOTENCY_KEY_REUSED"
        )
        foreign_row = (await create(other)).json()["data"]
        async with app.state.session_factory() as db:
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        update(Workflow)
                        .where(Workflow.organization_id == UUID(who["organizationId"]))
                        .values(created_by_user_id=UUID(foreign["id"]))
                    )
            with pytest.raises(IntegrityError):
                async with db.begin_nested():
                    await db.execute(
                        update(WorkflowKey)
                        .where(WorkflowKey.organization_id == UUID(who["organizationId"]))
                        .values(workflow_id=UUID(foreign_row["id"]))
                    )
            await db.rollback()


async def test_same_tenant_operator_replay_does_not_transfer_ownership(app):
    from app.domain.auth.service import issue_session

    async with client(app) as owner, client(app) as coworker:
        identity = await signup(owner)
        async with app.state.session_factory() as db:
            await db.execute(
                update(User).where(User.id == UUID(identity["id"])).values(role="operator")
            )
            user = User(
                id=uuid4(),
                organization_id=UUID(identity["organizationId"]),
                display_name="Coworker",
                email="coworker@example.com",
                password_hash="unused",
                role="operator",
            )
            db.add(user)
            await db.flush()
            token, _ = await issue_session(db, user, 3600)
            await db.commit()
        coworker.cookies.set("virexa_session", token)
        key = str(uuid4())
        original = (await create(owner, key)).json()["data"]
        replay = await create(coworker, key)
        assert replay.status_code == 200
        assert replay.json()["data"] == original
        assert (await patch(coworker, original, name="Not mine")).status_code == 403
        changed = (await patch(owner, original, name="Owner's edit")).json()["data"]
        assert (await create(coworker, key)).json()["data"] == changed
        async with app.state.session_factory() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == "workflow.created")
                )
                == 1
            )


async def test_audit_failure_rolls_back_record_key_and_update(app, monkeypatch):
    from app.domain.workflows import service

    real_record = service.record
    async with client(app) as c:
        await signup(c)
        original = (await create(c)).json()["data"]

        async def fail_after_event(*args, **kwargs):
            await real_record(*args, **kwargs)
            raise SQLAlchemyError("Synthetic interruption after audit flush")

        monkeypatch.setattr(service, "record", fail_after_event)
        key = str(uuid4())
        assert (await create(c, key)).status_code == 503
        assert (await patch(c, original, name="Rolled back")).status_code == 503
        async with app.state.session_factory() as db:
            assert await db.scalar(select(func.count()).select_from(Workflow)) == 1
            assert await db.scalar(select(func.count()).select_from(WorkflowKey)) == 1
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.resource_type == "workflow")
                )
                == 1
            )
        assert (await c.get(f"{BASE}/{original['id']}")).json()["data"] == original
        monkeypatch.setattr(service, "record", real_record)
        assert (await create(c, key)).status_code == 201
