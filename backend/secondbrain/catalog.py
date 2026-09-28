"""Accesso al catalogo Postgres: engine, sessioni, query (spec §8)."""
import uuid

from sqlalchemy import create_engine, select
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
