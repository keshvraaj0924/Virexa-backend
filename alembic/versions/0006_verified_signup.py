"""Mailbox proof and individual/organization workspace kind; legacy tenants unchanged."""

import sqlalchemy as sa

from alembic import op

revision = "0006_verified_signup"
down_revision = "0005_usage_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "organizations",
        sa.Column("account_type", sa.String(16), nullable=False, server_default="organization"),
    )
    op.create_check_constraint(
        "ck_organization_account_type",
        "organizations",
        "account_type IN ('individual', 'organization')",
    )
    op.create_table(
        "signup_challenges",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(120), nullable=False),
        sa.Column("organization_name", sa.String(160), nullable=True),
        sa.Column("account_type", sa.String(16), nullable=False),
        sa.Column("code_digest", sa.String(64), nullable=True),
        sa.Column("setup_digest", sa.String(64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
    )


def downgrade():
    op.drop_table("signup_challenges")
    op.drop_constraint("ck_organization_account_type", "organizations", type_="check")
    op.drop_column("organizations", "account_type")
