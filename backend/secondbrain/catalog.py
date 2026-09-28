"""Accesso al catalogo Postgres: engine, sessioni, query (spec §8)."""
import uuid
from datetime import date

from sqlalchemy import Integer, cast, create_engine, extract, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Capture, Device


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def get_device(s: Session, device_id: str) -> Device | None:
    return s.get(Device, device_id)


def get_capture(s: Session, capture_id: uuid.UUID) -> Capture | None:
    return s.get(Capture, capture_id)


def find_captures(s: Session, device_id: str, capture_id: str) -> list[Capture]:
    """Tutte le registrazioni con quell'id del device, cestino compreso (spec §6 punto 5)."""
    stmt = (select(Capture)
            .where(Capture.device_id == device_id, Capture.capture_id == capture_id)
            .order_by(Capture.received_at, Capture.rel_path))
    return list(s.scalars(stmt))


def list_devices(s: Session) -> list[Device]:
    return list(s.scalars(select(Device).order_by(Device.name, Device.id)))


_YEAR = cast(extract("year", Capture.day), Integer)
_MONTH = cast(extract("month", Capture.day), Integer)
_DAY = cast(extract("day", Capture.day), Integer)


def _active(stmt, device_id: str | None):
    stmt = stmt.where(Capture.trashed_at.is_(None))
    return stmt.where(Capture.device_id == device_id) if device_id else stmt


def _counts(s: Session, stmt) -> list[tuple[int, int]]:
    return [(int(key), int(n)) for key, n in s.execute(stmt)]


def year_counts(s: Session, device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = select(_YEAR, func.count()).group_by(_YEAR).order_by(_YEAR.desc())
    return _counts(s, _active(stmt, device_id))


def month_counts(s: Session, year: int, device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = (select(_MONTH, func.count()).where(_YEAR == year)
            .group_by(_MONTH).order_by(_MONTH))
    return _counts(s, _active(stmt, device_id))


def day_counts(s: Session, year: int, month: int,
               device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = (select(_DAY, func.count()).where(_YEAR == year, _MONTH == month)
            .group_by(_DAY).order_by(_DAY))
    return _counts(s, _active(stmt, device_id))


def list_day(s: Session, day: date, device_id: str | None = None) -> list[Capture]:
    stmt = select(Capture).where(Capture.day == day).order_by(Capture.recorded_at)
    return list(s.scalars(_active(stmt, device_id)))


def count_estimated(s: Session) -> int:
    stmt = select(func.count()).select_from(Capture).where(Capture.date_estimated.is_(True))
    return s.scalar(_active(stmt, None))


def list_estimated(s: Session) -> list[Capture]:
    stmt = (select(Capture).where(Capture.date_estimated.is_(True))
            .order_by(Capture.recorded_at.desc()))
    return list(s.scalars(_active(stmt, None)))


def list_trashed(s: Session) -> list[Capture]:
    stmt = (select(Capture).where(Capture.trashed_at.is_not(None))
            .order_by(Capture.trashed_at.desc()))
    return list(s.scalars(stmt))
