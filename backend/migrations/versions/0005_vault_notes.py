"""note nel vault Obsidian: percorso e data di modifica del file

Revision ID: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("captures", sa.Column("note_path", sa.String(255), nullable=True))
    op.add_column("captures", sa.Column("note_mtime", sa.Double(), nullable=True))
    op.create_unique_constraint("uq_captures_note_path", "captures", ["note_path"])


def downgrade() -> None:
    op.drop_constraint("uq_captures_note_path", "captures", type_="unique")
    op.drop_column("captures", "note_mtime")
    op.drop_column("captures", "note_path")
