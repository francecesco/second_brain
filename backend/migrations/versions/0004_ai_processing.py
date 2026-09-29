"""elaborazione AI: campi delle note, coda, impostazioni, utilizzo

Revision ID: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

EMPTY_ARRAY = sa.text("'{}'")
AI_COLUMNS = ("transcript", "title_auto", "summary", "tags", "edited", "language", "ai_provider",
              "ai_transcribe_model", "ai_enrich_provider", "ai_enrich_model", "processed_at",
              "search_vector")


def upgrade() -> None:
    op.add_column("captures", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column("captures", sa.Column("title_auto", sa.String(200), nullable=True))
    op.add_column("captures", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("captures", sa.Column("tags", postgresql.ARRAY(sa.String(40)), nullable=False,
                                        server_default=EMPTY_ARRAY))
    op.add_column("captures", sa.Column("edited", postgresql.ARRAY(sa.String(16)), nullable=False,
                                        server_default=EMPTY_ARRAY))
    op.add_column("captures", sa.Column("language", sa.String(8), nullable=True))
    op.add_column("captures", sa.Column("ai_provider", sa.String(32), nullable=True))
    op.add_column("captures", sa.Column("ai_transcribe_model", sa.String(100), nullable=True))
    op.add_column("captures", sa.Column("ai_enrich_provider", sa.String(32), nullable=True))
    op.add_column("captures", sa.Column("ai_enrich_model", sa.String(100), nullable=True))
    op.add_column("captures", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("captures", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.create_index("ix_captures_search_vector", "captures", ["search_vector"],
                    postgresql_using="gin")
    op.create_index("ix_captures_tags", "captures", ["tags"], postgresql_using="gin")
    # Le note già archiviate hanno solo il titolo manuale; lingua di default = italiano.
    op.execute("UPDATE captures SET search_vector = "
               "setweight(to_tsvector('italian', coalesce(title, '')), 'A')")

    op.create_table(
        "jobs",
        sa.Column("capture_id", sa.Uuid(), sa.ForeignKey("captures.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "priority", "next_run_at"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "ai_providers",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("transcribe_model", sa.String(100), nullable=False),
        sa.Column("text_model", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_usage",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("audio_seconds", sa.Double(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("ai_providers")
    op.drop_table("settings")
    op.drop_table("jobs")
    op.drop_index("ix_captures_tags", table_name="captures")
    op.drop_index("ix_captures_search_vector", table_name="captures")
    for column in AI_COLUMNS:
        op.drop_column("captures", column)
