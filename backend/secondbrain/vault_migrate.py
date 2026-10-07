"""Migrazione delle note dall'archivio al vault (spec vault §9): una volta sola, idempotente."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .archive import NOTE_SUFFIX, Archive
from .models import Capture
from .notefile import NoteError, parse_note
from .notes import lock_capture, write_note
from .vault import Vault

log = logging.getLogger(__name__)


def migrate(s: Session, archive: Archive, vault: Vault, tz: ZoneInfo, public_url: str | None) -> int:
    """Per ogni registrazione fuori dal cestino con `<base>.md` nell'archivio: nota scritta nel
    vault con il nome atteso e `.md` tolto dall'archivio. Ritorna quante ne ha spostate."""
    moved = 0
    ids = s.scalars(select(Capture.id).where(Capture.trashed_at.is_(None))).all()
    for capture_id in ids:
        capture = lock_capture(s, capture_id)
        if capture is None or capture.trashed_at is not None:
            s.rollback()
            continue
        old = archive.derived_path(capture.rel_path, NOTE_SUFFIX)
        if not old.exists():
            s.rollback()
            continue
        try:
            note = parse_note(old.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, NoteError) as exc:
            log.warning("%s: nota non leggibile, lasciata nell'archivio (%s)", capture.rel_path, exc)
            s.rollback()
            continue
        write_note(s, archive, capture, note, tz, vault=vault, public_url=public_url)
        old.unlink()
        s.commit()
        moved += 1
        log.info("%s -> %s", capture.rel_path, capture.note_path)
    return moved
