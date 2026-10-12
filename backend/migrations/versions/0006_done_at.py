"""note da fare: istante in cui la registrazione e' stata spuntata come fatta

Revision ID: 0006
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("captures", sa.Column("done_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_captures_todo", "captures", ["recorded_at"],
                    postgresql_where=sa.text("done_at IS NULL AND trashed_at IS NULL"))


def downgrade() -> None:
    op.drop_index("ix_captures_todo", table_name="captures")
    op.drop_column("captures", "done_at")
