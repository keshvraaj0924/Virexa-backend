"""Optional version-bound local layout jobs; no automatic review approval."""
import sqlalchemy as sa

from alembic import op

revision = "0011_document_layout_jobs"
down_revision = "0010_document_scan_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_layout_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("pipeline_version", sa.String(100), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_code", sa.String(40)),
        sa.Column("page_count", sa.Integer()),
        sa.Column("result", sa.JSON()),
        sa.Column("page_previews", sa.JSON()),
        sa.UniqueConstraint("id", "organization_id", name="uq_layout_tenant_identity"),
        sa.UniqueConstraint("document_id", "pipeline_version", name="uq_layout_document_version"),
        sa.ForeignKeyConstraint(["document_id", "organization_id"],
                                ["documents.id", "documents.organization_id"]),
        sa.ForeignKeyConstraint(["actor_user_id", "organization_id"],
                                ["users.id", "users.organization_id"]),
        sa.CheckConstraint("state IN ('pending','running','needs_review','error')", name="ck_layout_state"),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 3", name="ck_layout_attempts"),
        sa.CheckConstraint("page_count IS NULL OR (page_count > 0 AND page_count <= 20)", name="ck_layout_pages"),
    )
    op.create_index("ix_document_layout_jobs_organization_id", "document_layout_jobs", ["organization_id"])
    op.create_index("ix_document_layout_jobs_available_at", "document_layout_jobs", ["available_at"])


def downgrade():
    op.drop_table("document_layout_jobs")
