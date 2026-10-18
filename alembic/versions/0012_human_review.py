"""Append-only, version-bound human layout reviews."""

import sqlalchemy as sa

from alembic import op

revision = "0012_human_review"
down_revision = "0011_document_layout_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("documents", sa.Column("current_layout_job_id", sa.Uuid(), nullable=True))
    # Existing single candidates are unambiguous. Never guess between several
    # historical candidates using timestamps; null remains fail-closed.
    op.execute("""UPDATE documents SET current_layout_job_id = (
        SELECT j.id FROM document_layout_jobs j
        WHERE j.document_id = documents.id AND j.organization_id = documents.organization_id
        LIMIT 1) WHERE 1 = (SELECT count(*) FROM document_layout_jobs j
        WHERE j.document_id = documents.id AND j.organization_id = documents.organization_id)""")
    op.create_table(
        "document_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("result_kind", sa.String(20), nullable=False),
        sa.Column("source_sha256", sa.String(64), nullable=False),
        sa.Column("result_sha256", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_compared", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(40), nullable=False),
        sa.Column("corrections", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "revision", name="uq_review_revision"),
        sa.UniqueConstraint("job_id", "idempotency_key", name="uq_review_key"),
        sa.ForeignKeyConstraint(
            ["job_id", "organization_id"],
            ["document_layout_jobs.id", "document_layout_jobs.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id", "organization_id"], ["users.id", "users.organization_id"]
        ),
        sa.CheckConstraint("status != 'approved' OR source_compared", name="ck_review_attestation"),
        sa.CheckConstraint("revision > 0", name="ck_review_revision"),
        sa.CheckConstraint("result_kind = 'layout'", name="ck_review_kind"),
        sa.CheckConstraint(
            "status IN ('corrected','approved','rejected')", name="ck_review_status"
        ),
    )
    op.create_index(
        "ix_document_review_events_organization_id", "document_review_events", ["organization_id"]
    )
    op.create_index("ix_document_review_events_job_id", "document_review_events", ["job_id"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION prevent_review_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Review history is immutable'; END;
            $$ LANGUAGE plpgsql""")
        op.execute("""CREATE TRIGGER review_history_immutable BEFORE UPDATE OR DELETE
            ON document_review_events FOR EACH ROW EXECUTE FUNCTION prevent_review_mutation()""")


def downgrade():
    op.drop_column("documents", "current_layout_job_id")
    op.drop_table("document_review_events")
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION prevent_review_mutation()")
