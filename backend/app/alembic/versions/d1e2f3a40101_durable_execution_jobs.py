"""Durable jobs, worker leases, fencing, cancellation and request idempotency."""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a40101"
down_revision = "c14e8f720ad1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "executionjob",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("idempotency_scope", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True)),
        sa.Column("deadline_at", sa.DateTime(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("kind", "target_id", name="uq_executionjob_target"),
        sa.UniqueConstraint(
            "owner_id",
            "kind",
            "idempotency_scope",
            "idempotency_key",
            name="uq_executionjob_idempotency",
        ),
    )
    for column in ("owner_id", "target_id", "status"):
        op.create_index(f"ix_executionjob_{column}", "executionjob", [column])
    op.create_index("ix_executionjob_claim", "executionjob", ["status", "created_at"])
    # Upgrade existing queued intents, but never replay work that was running.
    op.execute("""
        INSERT INTO executionjob
          (id, owner_id, kind, target_id, payload, status, idempotency_scope,
           request_hash, attempt_count, created_at)
        SELECT gen_random_uuid(), c.owner_id, 'assistant', t.id,
          json_build_object('content', m.content, 'knowledge_base_ids', c.knowledge_base_ids, 'use_web', c.use_web),
          'queued', c.id::text, '', 0, t.created_at
        FROM assistantturn t JOIN assistantconversation c ON c.id = t.conversation_id
        JOIN assistantmessage m ON m.turn_id = t.id AND m.role = 'user'
        WHERE t.status = 'queued'
    """)
    op.execute("""
        INSERT INTO executionjob
          (id, owner_id, kind, target_id, payload, status, idempotency_scope,
           request_hash, attempt_count, created_at)
        SELECT gen_random_uuid(), owner_id, 'research', id,
          json_build_object('query', query, 'knowledge_base_ids', knowledge_base_ids, 'use_web', use_web),
          'queued', '', '', 0, created_at
        FROM researchrun WHERE status = 'queued'
    """)
    for table, event_table, foreign_key in (
        ("assistantturn", "assistantevent", "turn_id"),
        ("researchrun", "researchevent", "run_id"),
    ):
        op.execute(
            sa.text(f"""
            WITH interrupted AS (
              UPDATE {table} SET status='failed', error_message='Execution interrupted during upgrade; the external-call outcome may be unknown. Send a new request to retry.', updated_at=now()
              WHERE status='running' RETURNING id
            )
            INSERT INTO {event_table} ({foreign_key}, event_type, payload, created_at)
            SELECT id, 'error', '{{"message":"Execution interrupted during upgrade; retry explicitly."}}'::json, now()
            FROM interrupted
        """)
        )


def downgrade():
    op.drop_table("executionjob")
