"""Persist cleared contact opt-outs and campaign creation request IDs."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision = "a711b250aa01"
down_revision = "58ad603ef21b"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "retained_email_suppressions",
        sa.Column("email", sa.String(254), primary_key=True),
    )
    op.create_table(
        "campaign_creation_requests",
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("request_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("signature", sa.String(64), nullable=False),
        sa.Column("excluded", postgresql.JSONB(), nullable=False),
    )


def downgrade():
    op.drop_table("campaign_creation_requests")
    op.drop_table("retained_email_suppressions")
