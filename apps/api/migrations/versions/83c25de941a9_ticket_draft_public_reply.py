"""Bind public reuse of private drafts to an explicit approval and subject."""

import sqlalchemy as sa
from alembic import op

revision = "83c25de941a9"
down_revision = "f3a10c28e765"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("ticket_drafts", sa.Column("reply_subject", sa.String(1000)))
    op.add_column(
        "ticket_drafts",
        sa.Column("public_reply_approved", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.create_check_constraint(
        "ck_ticket_draft_public_reply_scope",
        "ticket_drafts",
        "NOT public_reply_approved OR (kind = 'notes' AND body <> '' AND target_status = '' "
        "AND resolution_note = '' AND reply_subject IS NOT NULL)",
    )


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM ticket_drafts "
        "WHERE reply_subject IS NOT NULL OR public_reply_approved)"
    )):
        raise RuntimeError("Downgrade would discard ticket reply subjects and approval scope.")
    op.drop_constraint("ck_ticket_draft_public_reply_scope", "ticket_drafts", type_="check")
    op.drop_column("ticket_drafts", "public_reply_approved")
    op.drop_column("ticket_drafts", "reply_subject")
