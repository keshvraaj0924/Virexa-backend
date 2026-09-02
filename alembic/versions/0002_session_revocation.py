"""Add server-side session revocation timestamps.

Revision ID: 0002_session_revocation
Revises: 0001_identity_tenancy
"""

from alembic import op
import sqlalchemy as sa

revision = "0002_session_revocation"
down_revision = "0001_identity_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_sessions_revoked_at", "sessions", ["revoked_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sessions_revoked_at", table_name="sessions")
    op.drop_column("sessions", "revoked_at")
