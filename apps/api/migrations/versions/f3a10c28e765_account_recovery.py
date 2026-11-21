"""Add protected account recovery methods and retire primary-mailbox resets."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "f3a10c28e765"
down_revision = "b78b240cf321"
branch_labels = None
depends_on = None

OLD_TOKEN_SCOPE = (
    "(purpose = 'activate' AND assignment_id IS NOT NULL AND user_id IS NULL) OR "
    "(purpose = 'reset' AND user_id IS NOT NULL)"
)


def upgrade():
    op.create_table(
        "account_recovery_methods",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(12), nullable=False),
        sa.Column("encrypted_destination", sa.LargeBinary()),
        sa.Column("destination_key", sa.String(64)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.Column("preferred", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("user_id", "kind", "destination_key", name="uq_account_recovery_destination"),
        sa.CheckConstraint("kind IN ('email', 'sms')", name="ck_recovery_method_kind"),
        sa.CheckConstraint(
            "(removed_at IS NULL AND encrypted_destination IS NOT NULL AND destination_key IS NOT NULL) OR "
            "(removed_at IS NOT NULL AND encrypted_destination IS NULL AND destination_key IS NULL)",
            name="ck_recovery_destination_lifecycle",
        ),
        sa.CheckConstraint(
            "NOT preferred OR (verified_at IS NOT NULL AND removed_at IS NULL)",
            name="ck_recovery_preferred_is_active",
        ),
    )
    op.create_index("ix_account_recovery_methods_user_id", "account_recovery_methods", ["user_id"])
    op.create_index("ix_account_recovery_methods_created_at", "account_recovery_methods", ["created_at"])
    op.create_index(
        "uq_preferred_recovery_method", "account_recovery_methods", ["user_id"],
        unique=True, postgresql_where=sa.text("preferred"),
    )
    op.create_table(
        "account_recovery_rate_limits",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_account_recovery_rate_limits_expires_at", "account_recovery_rate_limits", ["expires_at"])
    op.add_column("account_tokens", sa.Column("recovery_method_id", pg.UUID(as_uuid=True)))
    op.create_foreign_key(
        "fk_account_token_recovery_method", "account_tokens", "account_recovery_methods", ["recovery_method_id"], ["id"],
    )
    op.create_index("ix_account_tokens_recovery_method_id", "account_tokens", ["recovery_method_id"])
    op.create_index(
        "uq_pending_recovery_verification", "account_tokens", ["recovery_method_id"],
        unique=True,
        postgresql_where=sa.text("purpose = 'verify_recovery' AND used_at IS NULL AND revoked_at IS NULL AND expired_at IS NULL"),
    )
    op.execute(
        "UPDATE account_tokens SET revoked_at = now() WHERE purpose = 'reset' "
        "AND used_at IS NULL AND revoked_at IS NULL AND expired_at IS NULL"
    )
    op.drop_constraint("ck_account_token_scope", "account_tokens", type_="check")
    op.create_check_constraint(
        "ck_account_token_scope", "account_tokens",
        "(purpose = 'activate' AND assignment_id IS NOT NULL AND user_id IS NULL AND recovery_method_id IS NULL) OR "
        "(purpose = 'reset' AND user_id IS NOT NULL AND (recovery_method_id IS NOT NULL "
        "OR num_nonnulls(used_at, revoked_at, expired_at) > 0)) OR "
        "(purpose = 'verify_recovery' AND user_id IS NOT NULL AND recovery_method_id IS NOT NULL)",
    )
    op.add_column("account_reset_requests", sa.Column("recovery_kind", sa.String(12)))
    op.add_column("account_reset_requests", sa.Column("recovery_key", sa.String(64)))
    # These old ephemeral requests specified no verified backup destination.
    op.execute("DELETE FROM account_reset_requests")


def downgrade():
    if op.get_bind().scalar(sa.text(
        "SELECT EXISTS (SELECT 1 FROM account_recovery_methods) OR "
        "EXISTS (SELECT 1 FROM account_tokens WHERE recovery_method_id IS NOT NULL) OR "
        "EXISTS (SELECT 1 FROM account_reset_requests WHERE recovery_key IS NOT NULL)"
    )):
        raise RuntimeError("Downgrade would discard account recovery data. Preserve the recovery schema and history.")
    op.drop_column("account_reset_requests", "recovery_key")
    op.drop_column("account_reset_requests", "recovery_kind")
    op.drop_constraint("ck_account_token_scope", "account_tokens", type_="check")
    op.create_check_constraint("ck_account_token_scope", "account_tokens", OLD_TOKEN_SCOPE)
    op.drop_index("uq_pending_recovery_verification", table_name="account_tokens")
    op.drop_index("ix_account_tokens_recovery_method_id", table_name="account_tokens")
    op.drop_constraint("fk_account_token_recovery_method", "account_tokens", type_="foreignkey")
    op.drop_column("account_tokens", "recovery_method_id")
    op.drop_table("account_recovery_rate_limits")
    op.drop_table("account_recovery_methods")
