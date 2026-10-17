"""Accesso al catalogo Postgres: engine, sessioni, query (spec §8)."""
import uuid
from datetime import date, timedelta

from sqlalchemy import Integer, cast, create_engine, extract, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, selectinload, sessionmaker

from .jobs import ACTIVE, RUNNING
from .models import Capture, Device, Job

WEEK_DAYS = 7  # tessera "Ultimi 7 giorni" di Da fare


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


def _open(stmt):
    """Le completate escono dall'archivio e stanno nella vista "Completati" (spec note da fare §2)."""
    return stmt.where(Capture.done_at.is_(None))


def _counts(s: Session, stmt) -> list[tuple[int, int]]:
    return [(int(key), int(n)) for key, n in s.execute(stmt)]


def year_counts(s: Session, device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = select(_YEAR, func.count()).group_by(_YEAR).order_by(_YEAR.desc())
    return _counts(s, _open(_active(stmt, device_id)))


def month_counts(s: Session, year: int, device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = (select(_MONTH, func.count()).where(_YEAR == year)
            .group_by(_MONTH).order_by(_MONTH))
    return _counts(s, _open(_active(stmt, device_id)))


def day_counts(s: Session, year: int, month: int,
               device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = (select(_DAY, func.count()).where(_YEAR == year, _MONTH == month)
            .group_by(_DAY).order_by(_DAY))
    return _counts(s, _open(_active(stmt, device_id)))


def list_day(s: Session, day: date, device_id: str | None = None) -> list[Capture]:
    stmt = (select(Capture).where(Capture.day == day).order_by(Capture.recorded_at)
            .options(selectinload(Capture.job)))  # lo stato di ogni riga, in una query sola
    return list(s.scalars(_open(_active(stmt, device_id))))


def count_estimated(s: Session) -> int:
    stmt = select(func.count()).select_from(Capture).where(Capture.date_estimated.is_(True))
    return s.scalar(_active(stmt, None))


def list_estimated(s: Session) -> list[Capture]:
    stmt = (select(Capture).where(Capture.date_estimated.is_(True))
            .order_by(Capture.recorded_at.desc()).options(selectinload(Capture.job)))
    return list(s.scalars(_active(stmt, None)))


def count_todo(s: Session, device_id: str | None = None) -> int:
    """Registrazioni ancora da fare (spec note da fare §2): non spuntate, non nel cestino."""
    return s.scalar(_open(_active(select(func.count()).select_from(Capture), device_id)))


def list_todo(s: Session, device_id: str | None = None) -> list[Capture]:
    stmt = select(Capture).order_by(Capture.recorded_at.desc()).options(selectinload(Capture.job))
    return list(s.scalars(_open(_active(stmt, device_id))))


def todo_tiles(s: Session, device_id: str | None, today: date) -> dict:
    """Contatori delle tessere di "Da fare" (spec UI responsive §2.6): tutte, di oggi, degli
    ultimi 7 giorni (oggi compreso) e quelle con un'elaborazione in coda o in corso."""
    base = _open(_active(select(func.count()).select_from(Capture), device_id))
    week_start = today - timedelta(days=WEEK_DAYS - 1)
    jobs = base.join(Job, Job.capture_id == Capture.id)
    running = s.scalar(jobs.where(Job.status == RUNNING))
    return {
        "todo": s.scalar(base),
        "today": s.scalar(base.where(Capture.day == today)),
        "week": s.scalar(base.where(Capture.day >= week_start, Capture.day <= today)),
        "processing": s.scalar(jobs.where(Job.status.in_(ACTIVE))),
        "running": bool(running),
    }


def _done(stmt):
    return stmt.where(Capture.done_at.is_not(None))


def count_done(s: Session, device_id: str | None = None) -> int:
    return s.scalar(_done(_active(select(func.count()).select_from(Capture), device_id)))


def list_done(s: Session, device_id: str | None = None) -> list[Capture]:
    """Completate, dall'ultima spuntata."""
    stmt = select(Capture).order_by(Capture.done_at.desc()).options(selectinload(Capture.job))
    return list(s.scalars(_done(_active(stmt, device_id))))


def list_trashed(s: Session) -> list[Capture]:
    stmt = (select(Capture).where(Capture.trashed_at.is_not(None))
            .order_by(Capture.trashed_at.desc()))
    return list(s.scalars(stmt))
