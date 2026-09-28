"""Modelli SQLAlchemy del catalogo (spec §8)."""
import uuid
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Double, ForeignKey, Index,
                        Integer, String, Uuid)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_firmware: Mapped[str | None] = mapped_column(String(32))
    last_battery_pct: Mapped[int | None] = mapped_column(Integer)
    last_battery_v: Mapped[float | None] = mapped_column(Double)
    last_power_source: Mapped[str | None] = mapped_column(String(16))


class Capture(Base):
    __tablename__ = "captures"
    __table_args__ = (
        Index("ix_captures_device_capture", "device_id", "capture_id"),
        Index("ix_captures_recorded_at", "recorded_at"),
        Index("ix_captures_day", "day"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), ForeignKey("devices.id"))
    capture_id: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_estimated: Mapped[bool] = mapped_column(Boolean)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day: Mapped[date] = mapped_column(Date)
    rel_path: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str | None] = mapped_column(String(200))
    duration_s: Mapped[float] = mapped_column(Double)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    firmware_version: Mapped[str | None] = mapped_column(String(32))
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    battery_v: Mapped[float | None] = mapped_column(Double)
    power_source: Mapped[str | None] = mapped_column(String(16))
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
