"""azioni estratte dalle note: copia nel catalogo della lista `actions` del `.md`

Revision ID: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("captures", sa.Column("actions", JSONB, nullable=False,
                                        server_default=sa.text("'[]'::jsonb")))


def downgrade() -> None:
    op.drop_column("captures", "actions")
