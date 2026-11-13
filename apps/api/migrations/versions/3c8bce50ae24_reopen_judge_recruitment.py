"""Record when a recruitment request is reopened without changing past assignments."""

import sqlalchemy as sa
from alembic import op

revision = "3c8bce50ae24"
down_revision = "a20b4714c302"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("pilot_nominations", sa.Column("reopened_at", sa.DateTime(timezone=True)))


def downgrade():
    op.drop_column("pilot_nominations", "reopened_at")
