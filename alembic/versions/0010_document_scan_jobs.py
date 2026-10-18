"""Durable internal quarantine scan leases; old uploads remain quarantined."""

import sqlalchemy as sa

from alembic import op

revision = "0010_document_scan_jobs"
down_revision = "0009_member_versions"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "document_scan_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), sa.ForeignKey("document_uploads.id"), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result_code", sa.String(40)),
        sa.UniqueConstraint("document_id", name="uq_scan_document"),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"], ["documents.id", "documents.organization_id"]
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        sa.CheckConstraint(
            "state IN ('pending','running','clean','rejected','error')", name="ck_scan_job_state"
        ),
        sa.CheckConstraint("attempts >= 0 AND attempts <= 3", name="ck_scan_job_attempts"),
    )
    op.create_index(
        "ix_document_scan_jobs_organization_id", "document_scan_jobs", ["organization_id"]
    )
    op.create_index("ix_document_scan_jobs_available_at", "document_scan_jobs", ["available_at"])


def downgrade():
    op.drop_table("document_scan_jobs")
