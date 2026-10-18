# ruff: noqa: F811
"""Tenant isolation, real sessions, atomic audit and PostgreSQL lock contracts."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, update
from test_document_intake import ORIGIN, app, client, signup  # noqa: F401

from app.core.auth_context import AuthContext
from app.core.rbac import Role
from app.db.models import Organization, User
from app.domain.audit.models import AuditEvent
from app.domain.organization import service
from app.domain.organization.schemas import ChangeRole

BASE = "/api/v1/organization"


async def coworker(app, who, role="viewer"):
    async with app.state.session_factory() as db:
        row = User(
            organization_id=UUID(who["organizationId"]),
            display_name="Synthetic coworker",
            email=f"{uuid4()}@example.com",
            password_hash="unused",
            role=role,
        )
        db.add(row)
        await db.commit()
        return service.public(row)


async def change(c, member, role="manager", **kwargs):
    return await c.patch(
        f"{BASE}/members/{member['id']}/role",
        headers=ORIGIN,
        json={"role": role, "expectedVersion": member["version"], **kwargs},
    )


async def test_roster_pagination_detail_no_foreign_email_and_hierarchy(app):
    async with client(app) as c, client(app) as foreign:
        who = await signup(c)
        outsider = await signup(foreign, "outsider@example.com")
        others = [await coworker(app, who) for _ in range(4)]
        seen, cursor = [], None
        while True:
            params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
            result = await c.get(f"{BASE}/members", params=params)
            assert result.status_code == 200
            assert "no-store" in result.headers["cache-control"]
            assert "outsider@example.com" not in result.text
            body = result.json()["data"]
            seen.extend(row["id"] for row in body["items"])
            cursor = body["nextCursor"]
            if not cursor:
                break
        assert len(seen) == len(set(seen)) == 5
        for row in others:
            detail = await c.get(f"{BASE}/members/{row['id']}")
            assert detail.json()["data"] == row
        for identity in [outsider["id"], str(uuid4())]:
            assert (await c.get(f"{BASE}/members/{identity}")).status_code == 404
            assert (await change(c, {"id": identity, "version": 1})).status_code == 404
        tree = (await c.get(f"{BASE}/hierarchy")).json()["data"]
        assert tree["organization"]["id"] == who["organizationId"]
        assert tree["branches"] == tree["departments"] == tree["personas"] == []
        assert (await c.get(f"{BASE}/members?cursor=bad")).status_code == 400
        for query in ["limit=0", "limit=101", "unknown=x"]:
            assert (await c.get(f"{BASE}/members?{query}")).status_code == 400


@pytest.mark.parametrize("role", list(Role))
async def test_role_matrix_and_fresh_permissions(app, role):
    async with client(app) as c:
        who = await signup(c)
        target = await coworker(app, who)
        async with app.state.session_factory() as db:
            await db.execute(update(User).where(User.id == UUID(who["id"])).values(role=role))
            await db.commit()
        read = role in {
            Role.PLATFORM_ADMIN,
            Role.TENANT_ADMIN,
            Role.ORGANIZATION_ADMIN,
            Role.AUDITOR,
        }
        write = role in {Role.PLATFORM_ADMIN, Role.TENANT_ADMIN, Role.ORGANIZATION_ADMIN}
        assert (await c.get(f"{BASE}/members")).status_code == (200 if read else 403)
        assert (await c.get(f"{BASE}/members/{target['id']}")).status_code == (200 if read else 403)
        assert (await c.get(f"{BASE}/hierarchy")).status_code == (200 if read else 403)
        caps = (await c.get("/api/v1/me/permissions")).json()["data"]["capabilities"]
        assert ("organization:members:read" in caps) == read
        assert ("organization:members:manage" in caps) == write
        assert (await change(c, target)).status_code == (200 if write else 403)


async def test_individual_has_no_team_capabilities_or_routes(app):
    async with client(app) as c:
        who = await signup(c)
        target = await coworker(app, who)
        async with app.state.session_factory() as db:
            await db.execute(
                update(Organization)
                .where(Organization.id == UUID(who["organizationId"]))
                .values(account_type="individual")
            )
            await db.commit()
        caps = (await c.get("/api/v1/me/permissions")).json()["data"]["capabilities"]
        assert not any(item.startswith("organization:members:") for item in caps)
        assert "organization:hierarchy:read" not in caps
        assert (await c.get(f"{BASE}/members")).status_code == 403
        assert (await c.get(f"{BASE}/hierarchy")).status_code == 403
        assert (await change(c, target)).status_code == 403


async def test_version_conflict_self_platform_origin_and_transactional_audit(app):
    async with client(app) as c:
        who = await signup(c)
        target = await coworker(app, who)
        no_origin = await c.patch(
            f"{BASE}/members/{target['id']}/role",
            json={
                "role": "manager",
                "expectedVersion": 1,
            },
        )
        assert no_origin.status_code == 403
        assert (await change(c, target, "platform_admin")).status_code == 400
        assert (await change(c, target, organizationId=str(uuid4()))).status_code == 400
        assert (await change(c, {**who, "version": 1})).json()["error"][
            "code"
        ] == "SELF_ROLE_CHANGE_FORBIDDEN"
        elevated = await coworker(app, who, "platform_admin")
        assert (await change(c, elevated)).status_code == 403
        changed = await change(c, target)
        assert changed.status_code == 200
        assert changed.json()["data"]["version"] == 2
        assert (await change(c, target)).json()["error"]["code"] == "MEMBER_CONFLICT"
        assert (await change(c, changed.json()["data"])).json()["data"]["version"] == 2
        async with app.state.session_factory() as db:
            rows = list(
                await db.scalars(
                    select(AuditEvent).where(
                        AuditEvent.action == "organization.member.role_updated"
                    )
                )
            )
            assert len(rows) == 1
            assert rows[0].details == {"outcome": "succeeded"}
            assert rows[0].resource_id == target["id"]


async def test_last_admin_protected_even_from_platform_admin(app):
    async with client(app) as c:
        who = await signup(c)
        only_admin = await coworker(app, who, "organization_admin")
        async with app.state.session_factory() as db:
            await db.execute(
                update(User).where(User.id == UUID(who["id"])).values(role="platform_admin")
            )
            await db.commit()
        result = await change(c, only_admin)
        assert result.json()["error"]["code"] == "LAST_ORGANIZATION_ADMIN"
        await coworker(app, who, "tenant_admin")
        assert (await change(c, only_admin)).status_code == 200


async def test_role_change_is_visible_in_existing_session(app):
    async with client(app) as admin, client(app) as member:
        who = await signup(admin)
        target = await coworker(app, who, "organization_admin")
        # Synthetic test-only password provisioning, never an administration API.
        from app.core.security import hash_password

        async with app.state.session_factory() as db:
            await db.execute(
                update(User)
                .where(User.id == UUID(target["id"]))
                .values(password_hash=hash_password("synthetic-long-password"))
            )
            await db.commit()
        login = await member.post(
            "/api/v1/auth/login",
            headers=ORIGIN,
            json={
                "email": target["email"],
                "password": "synthetic-long-password",
            },
        )
        assert login.status_code == 200
        assert (await member.get(f"{BASE}/members")).status_code == 200
        assert (await change(admin, target, "viewer")).status_code == 200
        assert (await member.get(f"{BASE}/members")).status_code == 403
        assert (
            "organization:members:manage"
            not in (await member.get("/api/v1/me/permissions")).json()["data"]["capabilities"]
        )


async def test_failed_audit_rolls_back_role(app, monkeypatch):
    async with client(app) as c:
        who = await signup(c)
        target = await coworker(app, who)

        async def fail(*args, **kwargs):
            raise RuntimeError("synthetic audit failure")

        monkeypatch.setattr(service, "record", fail)
        with pytest.raises(RuntimeError):
            await change(c, target)
        async with app.state.session_factory() as db:
            row = await db.get(User, UUID(target["id"]))
            assert row.role == "viewer" and row.version == 1


async def test_postgres_reciprocal_demotion_serializes_and_refreshes_actor(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    async with client(app) as c:
        who = await signup(c)
        target = await coworker(app, who, "organization_admin")
        organization_id = UUID(who["organizationId"])

        async def demote(actor_id, target_id):
            context = AuthContext(UUID(actor_id), organization_id, Role.ORGANIZATION_ADMIN)
            async with app.state.session_factory() as db:
                try:
                    await service.change_role(
                        db,
                        context,
                        UUID(target_id),
                        ChangeRole(
                            role="viewer",
                            expectedVersion=1,
                        ),
                        str(uuid4()),
                    )
                    return 200
                except service.ApiError as error:
                    await db.rollback()
                    return error.status

        outcomes = await asyncio.gather(
            demote(who["id"], target["id"]), demote(target["id"], who["id"])
        )
        assert sorted(outcomes) == [200, 403]
        async with app.state.session_factory() as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(User)
                    .where(
                        User.organization_id == organization_id, User.role.in_(service.ADMIN_ROLES)
                    )
                )
                == 1
            )


@pytest.mark.parametrize(
    "role", ["tenant_admin", "organization_admin", "manager", "operator", "viewer", "auditor"]
)
async def test_each_permitted_tenant_role_can_be_assigned(app, role):
    async with client(app) as c:
        who = await signup(c)
        member = await coworker(app, who, "operator" if role == "viewer" else "viewer")
        response = await change(c, member, role)
        assert response.status_code == 200
        assert response.json()["data"]["role"] == role
        assert response.json()["data"]["version"] == 2


async def test_postgres_concurrent_platform_demotions_keep_last_tenant_admin(app):
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("Requires PostgreSQL row locks")
    async with client(app) as c:
        who = await signup(c)
        first = await coworker(app, who, "organization_admin")
        second = await coworker(app, who, "tenant_admin")
        async with app.state.session_factory() as db:
            await db.execute(
                update(User).where(User.id == UUID(who["id"])).values(role="platform_admin")
            )
            await db.commit()
        outcomes = await asyncio.gather(change(c, first), change(c, second))
        assert sorted(response.status_code for response in outcomes) == [200, 409]
        failure = next(response for response in outcomes if response.status_code == 409)
        assert failure.json()["error"]["code"] == "LAST_ORGANIZATION_ADMIN"
