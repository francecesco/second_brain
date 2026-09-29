"""Modelli SQLAlchemy del catalogo (spec archivio §8, spec elaborazione AI §7)."""
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Double, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, Uuid, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMPTY_ARRAY = text("'{}'")


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
        Index("ix_captures_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_captures_tags", "tags", postgresql_using="gin"),
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
    # Campi AI (spec AI §6-7): la verità è il `.md` accanto al WAV, qui sono una copia
    # per la UI e la ricerca, ricostruibile con `rescan`.
    transcript: Mapped[str | None] = mapped_column(Text)
    title_auto: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list,
                                            server_default=EMPTY_ARRAY)
    edited: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list,
                                              server_default=EMPTY_ARRAY)
    language: Mapped[str | None] = mapped_column(String(8))
    ai_provider: Mapped[str | None] = mapped_column(String(32))
    ai_transcribe_model: Mapped[str | None] = mapped_column(String(100))
    ai_enrich_provider: Mapped[str | None] = mapped_column(String(32))
    ai_enrich_model: Mapped[str | None] = mapped_column(String(100))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, deferred=True)
    # Sola lettura: il lavoro si cancella con la nota grazie a ON DELETE CASCADE nel DB.
    job: Mapped[Optional["Job"]] = relationship(viewonly=True, uselist=False)


class Job(Base):
    """Un lavoro di elaborazione per nota (spec AI §7)."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "priority", "next_run_at"),)

    capture_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("captures.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str] = mapped_column(String(16))
    priority: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    """Impostazioni dell'elaborazione: chiave → valore JSON (spec AI §8)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)


class AiProvider(Base):
    __tablename__ = "ai_providers"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    position: Mapped[int] = mapped_column(Integer)
    api_key_enc: Mapped[str | None] = mapped_column(Text)  # token Fernet, mai la chiave in chiaro
    transcribe_model: Mapped[str] = mapped_column(String(100))
    text_model: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AiUsage(Base):
    """Utilizzo per provider e mese: secondi di audio trascritti e chiamate."""
    __tablename__ = "ai_usage"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    audio_seconds: Mapped[float] = mapped_column(Double, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)


class FirmwareRelease(Base):
    __tablename__ = "firmware_releases"
    __table_args__ = (UniqueConstraint("type", "version", name="uq_firmware_type_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    file: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current: Mapped[bool] = mapped_column(Boolean)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_at", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(String(64))
