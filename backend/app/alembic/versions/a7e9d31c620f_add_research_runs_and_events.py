"""Add research runs and replayable events to the shared application database."""

import sqlalchemy as sa
from alembic import op

revision = "a7e9d31c620f"
down_revision = "4d71c8155b7f"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "researchrun",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("query", sa.String(4000), nullable=False),
        sa.Column("knowledge_base_ids", sa.JSON(), nullable=False),
        sa.Column("use_web", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("final_report", sa.Text(), nullable=False),
        sa.Column("error_message", sa.String(1000)),
        sa.Column("sub_tasks", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_researchrun_owner_id", "researchrun", ["owner_id"])
    op.create_index("ix_researchrun_status", "researchrun", ["status"])
    op.create_table(
        "researchevent",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.Uuid(), sa.ForeignKey("researchrun.id", ondelete="CASCADE"), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_researchevent_run_id", "researchevent", ["run_id"])


def downgrade():
    op.drop_table("researchevent")
    op.drop_table("researchrun")
