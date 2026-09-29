"""Operazioni sulle registrazioni che toccano disco e catalogo (spec §4, §7).

Ordine sempre uguale: prima il disco (file e sidecar), poi il commit. Se il commit
fallisce il disco ha già ragione e `rescan` riallinea il catalogo.
"""
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .archive import TRASH, Archive
from .models import Capture
from .naming import day_dir, local_day
from .notefile import MAX_TITLE_LEN
from .sidecar import capture_to_sidecar


class NotFound(LookupError):
    """Registrazione sconosciuta."""


class InTrash(ValueError):
    """Operazione non permessa su una registrazione nel cestino."""


class NotInTrash(ValueError):
    """Operazione permessa solo dal cestino."""


def _get(s: Session, capture_id: uuid.UUID) -> Capture:
    capture = s.get(Capture, capture_id)
    if capture is None:
        raise NotFound(str(capture_id))
    return capture


def _save(s: Session, archive: Archive, capture: Capture) -> None:
    archive.write_sidecar(capture.rel_path, capture_to_sidecar(capture))
    s.commit()


def set_title(s: Session, archive: Archive, capture_id: uuid.UUID, title: str) -> Capture:
    capture = _get(s, capture_id)
    capture.title = title.strip()[:MAX_TITLE_LEN] or None
    _save(s, archive, capture)
    return capture


def correct_recorded_at(s: Session, archive: Archive, tz: ZoneInfo, capture_id: uuid.UUID,
                        local: datetime) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        raise InTrash("la registrazione è nel cestino")
    new_utc = local.replace(tzinfo=tz).astimezone(UTC)
    new_day = local_day(new_utc, tz)
    if new_day != capture.day:
        capture.rel_path = archive.move(capture.rel_path, day_dir(new_day))
        capture.day = new_day
    capture.recorded_at = new_utc
    capture.date_estimated = False
    _save(s, archive, capture)
    return capture


def trash_capture(s: Session, archive: Archive, capture_id: uuid.UUID, now: datetime) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is None:
        capture.rel_path = archive.move(capture.rel_path, f"{TRASH}/{day_dir(capture.day)}")
        capture.trashed_at = now
        _save(s, archive, capture)
    return capture


def restore_capture(s: Session, archive: Archive, capture_id: uuid.UUID) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        capture.rel_path = archive.move(capture.rel_path, day_dir(capture.day))
        capture.trashed_at = None
        _save(s, archive, capture)
    return capture


def delete_capture(s: Session, archive: Archive, capture_id: uuid.UUID) -> None:
    capture = _get(s, capture_id)
    if capture.trashed_at is None:
        raise NotInTrash("si elimina definitivamente solo dal cestino")
    archive.delete(capture.rel_path)
    s.delete(capture)
    s.commit()


def purge_trash(s: Session, archive: Archive, now: datetime, retention_days: int) -> int:
    limit = now - timedelta(days=retention_days)
    expired = s.scalars(select(Capture).where(Capture.trashed_at.is_not(None),
                                              Capture.trashed_at < limit)).all()
    for capture in expired:
        archive.delete(capture.rel_path)
        s.delete(capture)
    s.commit()
    return len(expired)
