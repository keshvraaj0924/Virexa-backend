"""Workflow tracking records, not an execution engine (separate Python database)."""

import sqlalchemy as sa

from alembic import op

revision = "0008_workflow_records"
down_revision = "0007_audit_events"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflows",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_workflow_tenant_identity"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_workflow_tenant_creator",
        ),
        sa.CheckConstraint(
            "status IN ('draft','active','paused','archived')", name="ck_workflow_status"
        ),
        sa.CheckConstraint("version > 0", name="ck_workflow_version"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 120", name="ck_workflow_name"),
    )
    op.create_index(
        "ix_workflow_tenant_created", "workflows", ["organization_id", "created_at", "id"]
    )
    op.create_table(
        "workflow_idempotency_keys",
        sa.Column(
            "organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), primary_key=True
        ),
        sa.Column("idempotency_key", sa.String(255), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("workflow_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["workflow_id", "organization_id"],
            ["workflows.id", "workflows.organization_id"],
            name="fk_workflow_key_tenant",
        ),
    )


def downgrade():
    op.drop_table("workflow_idempotency_keys")
    op.drop_table("workflows")
