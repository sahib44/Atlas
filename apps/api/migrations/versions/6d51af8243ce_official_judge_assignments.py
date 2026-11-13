"""Add competition activation and official judge assignment history."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "6d51af8243ce"
down_revision = "e72a6c05f184"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "pilot_competitions",
        sa.Column("activation_status", sa.String(30), nullable=False, server_default="active"),
    )
    op.create_check_constraint(
        "ck_competition_activation", "pilot_competitions",
        "activation_status IN ('active', 'inactive', 'pending_approval')",
    )
    op.create_unique_constraint("uq_competition_id_year", "pilot_competitions", ["id", "event_year"])

    uuid = postgresql.UUID(as_uuid=True)
    op.create_table(
        "official_judge_assignments",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("competition_id", uuid, nullable=False),
        sa.Column("event_year", sa.Integer(), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("judge_id", uuid, sa.ForeignKey("judge_contacts.id", ondelete="SET NULL")),
        sa.Column("judge_name", sa.String(400), nullable=False),
        sa.Column("confirmed_by", uuid, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by", uuid, sa.ForeignKey("users.id")),
        sa.Column("release_reason", sa.String(50)),
        sa.ForeignKeyConstraint(
            ["competition_id", "event_year"], ["pilot_competitions.id", "pilot_competitions.event_year"],
            name="fk_official_assignment_competition_year",
        ),
        sa.CheckConstraint("slot_number BETWEEN 1 AND 3", name="ck_official_assignment_slot"),
        sa.CheckConstraint("released_at IS NOT NULL OR judge_id IS NOT NULL", name="ck_official_assignment_live_judge"),
    )
    for column in ("competition_id", "judge_id", "created_at"):
        op.create_index(f"ix_official_judge_assignments_{column}", "official_judge_assignments", [column])
    op.create_index(
        "uq_official_competition_slot", "official_judge_assignments", ["competition_id", "slot_number"],
        unique=True, postgresql_where=sa.text("released_at IS NULL"),
    )
    op.create_index(
        "uq_official_tournament_judge", "official_judge_assignments", ["event_year", "judge_id"],
        unique=True, postgresql_where=sa.text("released_at IS NULL"),
    )
    # Existing competition availability is preserved. Nomination responses remain
    # untouched, and only explicit confirmations create assignment records.


def downgrade():
    op.drop_table("official_judge_assignments")
    op.drop_constraint("uq_competition_id_year", "pilot_competitions", type_="unique")
    op.drop_constraint("ck_competition_activation", "pilot_competitions", type_="check")
    op.drop_column("pilot_competitions", "activation_status")
