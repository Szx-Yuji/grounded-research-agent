"""Persistent conversations for the supervising assistant and its delegated agents."""

import sqlalchemy as sa
from alembic import op

revision = "b63f8a20c914"
down_revision = "a7e9d31c620f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistantconversation",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
        sa.Column("use_web", sa.Boolean(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summarized_count", sa.Integer(), nullable=False),
        sa.Column("summary_sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistantconversation_owner_id", "assistantconversation", ["owner_id"])
    op.create_table(
        "assistantturn",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("assistantconversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistantturn_conversation_id", "assistantturn", ["conversation_id"])
    op.create_index("ix_assistantturn_status", "assistantturn", ["status"])
    op.create_index("ix_assistantturn_one_active", "assistantturn", ["conversation_id"], unique=True, postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_table(
        "assistantmessage",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("assistantconversation.id", ondelete="CASCADE"), nullable=False),
        sa.Column("turn_id", sa.Uuid(), sa.ForeignKey("assistantturn.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("turn_id", "role", name="uq_assistantmessage_turn_role"),
    )
    op.create_index("ix_assistantmessage_conversation_id", "assistantmessage", ["conversation_id"])
    op.create_index("ix_assistantmessage_turn_id", "assistantmessage", ["turn_id"])
    op.create_table(
        "assistantevent",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("turn_id", sa.Uuid(), sa.ForeignKey("assistantturn.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_assistantevent_turn_id", "assistantevent", ["turn_id"])


def downgrade():
    op.drop_table("assistantevent")
    op.drop_table("assistantmessage")
    op.drop_table("assistantturn")
    op.drop_table("assistantconversation")
