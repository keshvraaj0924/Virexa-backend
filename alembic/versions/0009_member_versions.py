"""Optimistic concurrency for existing organization member roles."""

import sqlalchemy as sa

from alembic import op

revision = "0009_member_versions"
down_revision = "0008_workflow_records"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("version", sa.Integer(), server_default="1", nullable=False))


def downgrade():
    op.drop_column("users", "version")
