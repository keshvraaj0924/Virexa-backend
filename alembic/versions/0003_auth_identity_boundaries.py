"""Pin sessions to their organization and disambiguate email-only login.

For the isolated Python schema only. Existing duplicate emails must be reconciled
before running this migration; do not adopt the Fastify schema with this chain.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0003_auth_identity_boundaries"
down_revision = "0002_session_revocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_users_email", "users", ["email"])
    op.add_column("sessions", sa.Column("organization_id", postgresql.UUID(as_uuid=True)))
    op.execute(
        "UPDATE sessions SET organization_id = users.organization_id "
        "FROM users WHERE sessions.user_id = users.id"
    )
    op.alter_column("sessions", "organization_id", nullable=False)
    op.create_foreign_key(
        "fk_sessions_organization",
        "sessions",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_sessions_organization_id", "sessions", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_sessions_organization_id", table_name="sessions")
    op.drop_constraint("fk_sessions_organization", "sessions", type_="foreignkey")
    op.drop_column("sessions", "organization_id")
    op.drop_constraint("uq_users_email", "users", type_="unique")
