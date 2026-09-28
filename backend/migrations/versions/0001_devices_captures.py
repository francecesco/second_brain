"""dispositivi e registrazioni

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_firmware", sa.String(32), nullable=True),
        sa.Column("last_battery_pct", sa.Integer(), nullable=True),
        sa.Column("last_battery_v", sa.Double(), nullable=True),
        sa.Column("last_power_source", sa.String(16), nullable=True),
    )
    op.create_table(
        "captures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.String(32), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("capture_id", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_estimated", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rel_path", sa.String(255), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("duration_s", sa.Double(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("firmware_version", sa.String(32), nullable=True),
        sa.Column("battery_pct", sa.Integer(), nullable=True),
        sa.Column("battery_v", sa.Double(), nullable=True),
        sa.Column("power_source", sa.String(16), nullable=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_captures_device_capture", "captures", ["device_id", "capture_id"])
    op.create_index("ix_captures_recorded_at", "captures", ["recorded_at"])
    op.create_index("ix_captures_day", "captures", ["day"])


def downgrade() -> None:
    op.drop_table("captures")
    op.drop_table("devices")
