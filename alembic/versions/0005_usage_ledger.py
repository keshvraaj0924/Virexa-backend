"""Internal preview subscription periods and durable usage reservations."""

import sqlalchemy as sa

from alembic import op

revision = "0005_usage_ledger"
down_revision = "0004_document_quarantine"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint("uq_user_tenant_identity", "users", ["id", "organization_id"])
    op.create_table(
        "usage_periods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("plan", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("upload_limit", sa.Integer(), nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_usage_period_tenant"),
        sa.UniqueConstraint("organization_id", "starts_at", name="uq_usage_period"),
        sa.CheckConstraint("ends_at > starts_at", name="ck_usage_period_bounds"),
        sa.CheckConstraint("upload_limit >= 0", name="ck_usage_upload_limit"),
    )
    op.create_index("ix_usage_periods_organization_id", "usage_periods", ["organization_id"])
    op.create_table(
        "usage_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("period_id", sa.Uuid(), sa.ForeignKey("usage_periods.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("meter", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("amount", sa.Numeric(20, 8)),
        sa.Column("currency", sa.String(3)),
        sa.Column("pricing_version", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id", "organization_id"],
            ["users.id", "users.organization_id"],
            name="fk_usage_user_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["period_id", "organization_id"],
            ["usage_periods.id", "usage_periods.organization_id"],
            name="fk_usage_period_tenant",
        ),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_usage_event_key"),
        sa.CheckConstraint("quantity >= 0", name="ck_usage_quantity"),
        sa.CheckConstraint(
            "state IN ('reserved','succeeded','failed','released')", name="ck_usage_state"
        ),
        sa.CheckConstraint(
            "meter IN ('document_uploads','ocr_pages','workflow_runs','input_tokens','output_tokens','cached_tokens')",
            name="ck_usage_meter",
        ),
        sa.CheckConstraint("amount IS NULL OR amount >= 0", name="ck_usage_amount"),
        sa.CheckConstraint(
            "(amount IS NULL AND currency IS NULL AND pricing_version IS NULL) OR (amount IS NOT NULL AND currency IS NOT NULL AND pricing_version IS NOT NULL)",
            name="ck_usage_pricing",
        ),
    )
    for column in ("organization_id", "user_id", "period_id", "created_at"):
        op.create_index(f"ix_usage_events_{column}", "usage_events", [column])


def downgrade():
    op.drop_table("usage_events")
    op.drop_table("usage_periods")

    op.drop_constraint("uq_user_tenant_identity", "users", type_="unique")
