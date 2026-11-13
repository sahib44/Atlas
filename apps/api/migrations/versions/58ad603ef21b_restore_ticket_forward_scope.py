"""Restore ticket ownership for existing sandbox forward chains."""

from alembic import op

revision = "58ad603ef21b"
down_revision = "3c8bce50ae24"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        WITH RECURSIVE ticket_forwards(id, ticket_id) AS (
            SELECT id, related_ticket_id
            FROM outbound_messages
            WHERE related_ticket_id IS NOT NULL
            UNION
            SELECT child.id, parent.ticket_id
            FROM outbound_messages AS child
            JOIN ticket_forwards AS parent ON child.forwarded_from_id = parent.id
            WHERE child.related_ticket_id IS NULL
        )
        UPDATE outbound_messages AS message
        SET related_ticket_id = chain.ticket_id
        FROM ticket_forwards AS chain
        WHERE message.id = chain.id AND message.related_ticket_id IS NULL
    """)
    op.execute("""
        UPDATE audit_events AS audit
        SET entity_type = 'tickets', entity_id = message.related_ticket_id::text
        FROM outbound_messages AS message
        WHERE audit.action = 'message.forwarded' AND audit.entity_type = 'outbound_messages'
          AND audit.entity_id = message.id::text AND message.related_ticket_id IS NOT NULL
    """)
    op.execute("""
        UPDATE notifications AS alert
        SET ticket_id = message.related_ticket_id,
            href = '/tickets/' || message.related_ticket_id::text
        FROM outbound_messages AS message
        WHERE alert.outbound_id = message.id
          AND alert.ticket_id IS NULL AND message.related_ticket_id IS NOT NULL
    """)


def downgrade():
    # Keep restored access restrictions if this data-only migration is rolled back.
    pass
