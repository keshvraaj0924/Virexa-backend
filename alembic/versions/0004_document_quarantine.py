"""Private document metadata and immutable quarantine upload receipts.

Isolated Python schema only; never run against the legacy Fastify schema.
"""

import sqlalchemy as sa

from alembic import op

revision = "0004_document_quarantine"
down_revision = "0003_auth_identity_boundaries"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("original_file_name", sa.String(255), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("external_reference", sa.String(255)),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("scan_status", sa.String(30), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("id", "organization_id", name="uq_document_tenant_identity"),
        sa.UniqueConstraint("organization_id", "checksum_sha256", name="uq_document_checksum"),
        sa.UniqueConstraint("organization_id", "external_reference", name="uq_document_reference"),
        sa.CheckConstraint("size_bytes > 0 AND size_bytes <= 26214400", name="ck_document_size"),
        sa.CheckConstraint(
            "status IN ('received','processing','review_required','completed','failed')",
            name="ck_document_status",
        ),
        sa.CheckConstraint(
            "scan_status IN ('awaiting_upload','pending_scan','clean','rejected')",
            name="ck_document_scan",
        ),
    )
    op.create_index("ix_documents_organization_id", "documents", ["organization_id"])
    op.create_table(
        "document_uploads",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("organization_id", sa.Uuid(), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("bytes_verified", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("document_id", name="uq_upload_document"),
        sa.ForeignKeyConstraint(
            ["document_id", "organization_id"],
            ["documents.id", "documents.organization_id"],
            name="fk_upload_tenant_document",
        ),
        sa.CheckConstraint("status IN ('initiated','completed','failed')", name="ck_upload_status"),
        sa.UniqueConstraint("organization_id", "idempotency_key", name="uq_upload_idempotency"),
    )
    op.create_index("ix_document_uploads_organization_id", "document_uploads", ["organization_id"])


def downgrade():
    op.drop_table("document_uploads")
    op.drop_table("documents")
