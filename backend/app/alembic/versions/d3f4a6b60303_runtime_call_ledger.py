"""Record actual provider requests and tool spans without prompt bodies.

Revision ID: d3f4a6b60303
Revises: d2e3f4a50202
"""

from alembic import op
import sqlalchemy as sa

revision = "d3f4a6b60303"
down_revision = "d2e3f4a50202"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "runtimecall",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("job_id", sa.Uuid(), sa.ForeignKey("executionjob.id", ondelete="CASCADE"), nullable=False),
        sa.Column("owner_id", sa.Uuid(), sa.ForeignKey("user.id", ondelete="CASCADE"), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("model", sa.String(160), nullable=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("first_token_ms", sa.Float(), nullable=True),
        sa.Column("error_type", sa.String(80), nullable=True),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("reserved_output_tokens >= 0", name="ck_runtimecall_reservation"),
    )
    op.create_index("ix_runtimecall_job_started", "runtimecall", ["job_id", "started_at"])
    op.create_index("ix_runtimecall_owner_id", "runtimecall", ["owner_id"])
    op.create_index("ix_runtimecall_parent_id", "runtimecall", ["parent_id"])


def downgrade():
    op.drop_table("runtimecall")
