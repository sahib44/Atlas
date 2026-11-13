"""Track review on existing competition nominations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "a20b4714c302"
down_revision = "6d51af8243ce"
branch_labels = None
depends_on = None


def upgrade():
    for name in ("requested_by", "reviewed_by"):
        op.add_column("pilot_nominations", sa.Column(name, UUID(as_uuid=True), sa.ForeignKey("users.id")))
    op.add_column("pilot_nominations", sa.Column("review_status", sa.String(24)))
    op.add_column("pilot_nominations", sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.add_column("pilot_nominations", sa.Column("request_notes", sa.Text(), nullable=False, server_default=""))
    op.add_column("pilot_nominations", sa.Column("prior_involvement", sa.String(300), nullable=False, server_default=""))
    op.create_check_constraint("ck_nomination_review_status", "pilot_nominations",
                               "review_status IN ('pending', 'approved', 'rejected')")
    op.add_column("campaigns", sa.Column("event_year", sa.Integer()))
    op.add_column("campaigns", sa.Column("allow_declined_outreach", sa.Boolean(),
                                        nullable=False, server_default=sa.text("false")))


def downgrade():
    op.drop_column("campaigns", "allow_declined_outreach")
    op.drop_column("campaigns", "event_year")
    op.drop_constraint("ck_nomination_review_status", "pilot_nominations", type_="check")
    for name in ("prior_involvement", "request_notes", "reviewed_at", "review_status", "reviewed_by", "requested_by"):
        op.drop_column("pilot_nominations", name)
