"""Forward-only Python audit trail, matching the Node event column contract.

Python and Node migration chains target separate candidate databases. Do not run
this chain over a Node-managed database; no historical events are synthesized.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0007_audit_events"
down_revision = "0006_verified_signup"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "organization_id",
            sa.Uuid(),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("resource_type", sa.Text(), nullable=False),
        sa.Column("resource_id", sa.Text(), nullable=True),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("length(action) BETWEEN 1 AND 120", name="audit_events_action_check"),
        sa.CheckConstraint(
            "length(resource_type) BETWEEN 1 AND 120", name="audit_events_resource_type_check"
        ),
    )
    op.create_index(
        "audit_events_org_created_idx", "audit_events", ["organization_id", "created_at", "id"]
    )
    op.create_index(
        "audit_events_actor_created_idx", "audit_events", ["actor_user_id", "created_at"]
    )
    op.create_index(
        "audit_events_resource_idx",
        "audit_events",
        ["organization_id", "resource_type", "resource_id", "created_at"],
    )
    op.execute("REVOKE ALL ON audit_events FROM PUBLIC")


def downgrade():
    op.drop_table("audit_events")
