"""Keep sub-agent findings so later conversation turns can reuse them."""
import sqlalchemy as sa
from alembic import op

revision = "c14e8f720ad1"
down_revision = "b63f8a20c914"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("assistantturn", sa.Column("delegate_notes", sa.JSON(), nullable=False, server_default="[]"))
    op.alter_column("assistantturn", "delegate_notes", server_default=None)


def downgrade():
    op.drop_column("assistantturn", "delegate_notes")
