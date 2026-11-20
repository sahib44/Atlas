"""Add invitation-only accounts and a historical department assignment ledger."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "b78b240cf321"
down_revision = "a711b250aa01"
branch_labels = None
depends_on = None

DEPARTMENTS = (
    ("arts", "Arts", "arts"),
    ("knowledge-quran", "Knowledge & Quran", "knowledge-quran"),
    ("writing-oratory", "Writing & Oratory", "writing-oratory"),
    ("brackets", "Brackets", "brackets"),
    ("group-projects", "Group Projects", "group-projects"),
    ("brothers-sports", "Brothers Sports", "sports-referees"),
    ("sisters-sports", "Sisters Sports", "sports-referees"),
)


def records():
    return (
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade():
    op.add_column("users", sa.Column("managed_account", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_unique_constraint("uq_user_id_email", "users", ["id", "email"])
    op.create_check_constraint(
        "ck_managed_account_identity",
        "users",
        "NOT managed_account OR (email = lower(email) AND email ~ '^[^@[:space:]]+@getmistified[.]com$' "
        "AND role IN ('competitions_chair', 'competitions_member'))",
    )
    op.create_index(
        "uq_managed_competitions_lead", "users", ["role"], unique=True,
        postgresql_where=sa.text("managed_account AND role = 'competitions_chair'"),
    )
    departments = op.create_table(
        "departments",
        sa.Column("slug", sa.String(80), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("category_slug", sa.String(80), nullable=False),
        sa.Column("display_order", sa.Integer(), nullable=False),
    )
    op.bulk_insert(departments, [
        {"slug": slug, "name": name, "category_slug": category, "display_order": index}
        for index, (slug, name, category) in enumerate(DEPARTMENTS)
    ])
    op.create_table(
        "department_assignments",
        *records(),
        sa.Column("department_slug", sa.String(80), sa.ForeignKey("departments.slug"), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("end_reason", sa.String(40)),
        sa.ForeignKeyConstraint(["user_id", "email"], ["users.id", "users.email"], name="fk_department_assignment_identity"),
        sa.CheckConstraint("email = lower(email) AND email ~ '^[^@[:space:]]+@getmistified[.]com$'", name="ck_department_assignment_email"),
        sa.CheckConstraint(
            "(ended_at IS NULL AND end_reason IS NULL) OR "
            "(ended_at IS NOT NULL AND end_reason IS NOT NULL AND end_reason IN ('revoked', 'reassigned'))",
            name="ck_department_assignment_end",
        ),
    )
    for name, field in (
        ("uq_live_department_assignment", "department_slug"),
        ("uq_live_department_email", "email"),
        ("uq_live_department_user", "user_id"),
    ):
        op.create_index(name, "department_assignments", [field], unique=True, postgresql_where=sa.text("ended_at IS NULL"))
    for field in ("department_slug", "created_at"):
        op.create_index(f"ix_department_assignments_{field}", "department_assignments", [field])
    op.create_table(
        "account_tokens",
        *records(),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id")),
        sa.Column("assignment_id", pg.UUID(as_uuid=True), sa.ForeignKey("department_assignments.id")),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(purpose = 'activate' AND assignment_id IS NOT NULL AND user_id IS NULL) OR "
            "(purpose = 'reset' AND user_id IS NOT NULL)",
            name="ck_account_token_scope",
        ),
        sa.CheckConstraint("num_nonnulls(used_at, revoked_at, expired_at) <= 1", name="ck_account_token_single_terminal_state"),
    )
    for name, purpose, field in (
        ("uq_pending_invitation_token", "activate", "assignment_id"),
        ("uq_pending_reset_token", "reset", "user_id"),
    ):
        op.create_index(
            name, "account_tokens", [field], unique=True,
            postgresql_where=sa.text(
                f"purpose = '{purpose}' AND used_at IS NULL AND revoked_at IS NULL AND expired_at IS NULL"
            ),
        )
    for field in ("created_at", "user_id", "assignment_id", "expires_at"):
        op.create_index(f"ix_account_tokens_{field}", "account_tokens", [field])
    op.create_table(
        "account_reset_requests",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_reset_requests_created_at", "account_reset_requests", ["created_at"])
    op.add_column("outbound_messages", sa.Column("account_token_id", pg.UUID(as_uuid=True)))
    op.create_foreign_key("fk_outbound_account_token", "outbound_messages", "account_tokens", ["account_token_id"], ["id"])
    op.create_unique_constraint("uq_outbound_account_token", "outbound_messages", ["account_token_id"])


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM users WHERE managed_account) "
        "OR EXISTS (SELECT 1 FROM department_assignments) "
        "OR EXISTS (SELECT 1 FROM account_tokens) "
        "OR EXISTS (SELECT 1 FROM account_reset_requests)"
    )):
        raise RuntimeError("Downgrade would discard managed account history. Restore a pre-migration backup instead.")
    op.drop_constraint("uq_outbound_account_token", "outbound_messages", type_="unique")
    op.drop_constraint("fk_outbound_account_token", "outbound_messages", type_="foreignkey")
    op.drop_column("outbound_messages", "account_token_id")
    op.drop_table("account_reset_requests")
    op.drop_table("account_tokens")
    op.drop_table("department_assignments")
    op.drop_table("departments")
    op.drop_index("uq_managed_competitions_lead", table_name="users")
    op.drop_constraint("ck_managed_account_identity", "users", type_="check")
    op.drop_constraint("uq_user_id_email", "users", type_="unique")
    op.drop_column("users", "managed_account")
