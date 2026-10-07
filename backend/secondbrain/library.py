"""Operazioni sulle registrazioni che toccano disco e catalogo (spec §4, §7).

Ordine sempre uguale: prima il disco (file e sidecar), poi il commit. Se il commit
fallisce il disco ha già ragione e `rescan` riallinea il catalogo.
"""
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import jobs
from .archive import NOTE_SUFFIX, TRASH, Archive
from .models import Capture, Job
from .naming import day_dir, local_day
from .notefile import EDITABLE_FIELDS, MAX_TITLE_LEN, apply_edit
from .notes import lock_capture, note_from_capture, place_note, write_note
from .vault import Vault
from .search import refresh_search_vector
from .sidecar import capture_to_sidecar


class NotFound(LookupError):
    """Registrazione sconosciuta."""


class InTrash(ValueError):
    """Operazione non permessa su una registrazione nel cestino."""


class NotInTrash(ValueError):
    """Operazione permessa solo dal cestino."""


class NotProcessed(Exception):
    """La nota non ha ancora una trascrizione: riassunto e tag non sono ancora suoi (spec AI §10).

    Non è una `ValueError`: un `except ValueError` per il campo sconosciuto non deve
    inghiottirla per sbaglio."""


def _get(s: Session, capture_id: uuid.UUID) -> Capture:
    """La riga resta bloccata fino al commit: le operazioni su file e campi di una nota non
    si accavallano con la scrittura del worker (spec AI §10)."""
    capture = lock_capture(s, capture_id)
    if capture is None:
        raise NotFound(str(capture_id))
    return capture


def _save(s: Session, archive: Archive, capture: Capture) -> None:
    archive.write_sidecar(capture.rel_path, capture_to_sidecar(capture))
    refresh_search_vector(s, capture)  # il titolo manuale pesa nella ricerca
    s.commit()


def set_title(s: Session, archive: Archive, capture_id: uuid.UUID, title: str, *,
              vault: Vault | None = None, tz: ZoneInfo | None = None,
              public_url: str | None = None) -> Capture:
    capture = _get(s, capture_id)
    capture.title = title.strip()[:MAX_TITLE_LEN] or None
    if vault is not None and tz is not None:
        place_note(vault, capture, tz, public_url)   # il nome del file segue il titolo
    _save(s, archive, capture)
    return capture


def correct_recorded_at(s: Session, archive: Archive, tz: ZoneInfo, capture_id: uuid.UUID,
                        local: datetime, *, vault: Vault | None = None,
                        public_url: str | None = None) -> Capture:
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
    if vault is not None:
        place_note(vault, capture, tz, public_url)   # data e ora stanno nel nome e in `created`
    _save(s, archive, capture)
    return capture


def _note_to_archive(vault: Vault, archive: Archive, capture: Capture) -> None:
    """Nel cestino la nota sta accanto al WAV, fuori dal vault (spec vault §6)."""
    if capture.note_path and vault.mtime(capture.note_path) is not None:
        shutil.move(vault.abs(capture.note_path), archive.derived_path(capture.rel_path, NOTE_SUFFIX))
    capture.note_path = None
    capture.note_mtime = None


def _note_to_vault(vault: Vault, archive: Archive, capture: Capture, tz: ZoneInfo,
                   public_url: str | None) -> None:
    """Dal cestino al vault: il contenuto viene dal catalogo (allineato al file), il file
    accanto al WAV viene tolto."""
    place_note(vault, capture, tz, public_url)   # prima la copia nuova, poi via quella vecchia
    stale = archive.derived_path(capture.rel_path, NOTE_SUFFIX)
    if stale.exists():
        stale.unlink()


def trash_capture(s: Session, archive: Archive, capture_id: uuid.UUID, now: datetime, *,
                  vault: Vault | None = None, tz: ZoneInfo | None = None,
                  public_url: str | None = None) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is None:
        capture.rel_path = archive.move(capture.rel_path, f"{TRASH}/{day_dir(capture.day)}")
        capture.trashed_at = now
        if vault is not None:
            _note_to_archive(vault, archive, capture)
        jobs.cancel_for_trash(s, capture.id)
        _save(s, archive, capture)
    return capture


def restore_capture(s: Session, archive: Archive, capture_id: uuid.UUID, *,
                    vault: Vault | None = None, tz: ZoneInfo | None = None,
                    public_url: str | None = None) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        capture.rel_path = archive.move(capture.rel_path, day_dir(capture.day))
        capture.trashed_at = None
        if vault is not None and tz is not None:
            _note_to_vault(vault, archive, capture, tz, public_url)
        _save(s, archive, capture)
    return capture


def edit_ai_field(s: Session, archive: Archive, tz: ZoneInfo, capture_id: uuid.UUID, field: str,
                  value: str, *, vault: Vault | None = None, public_url: str | None = None) -> Capture:
    """Correzione a mano di trascrizione, riassunto o tag: `.md`, catalogo, `edited`."""
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"campo non modificabile: {field!r}")
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        raise InTrash("la registrazione è nel cestino")
    if field != "transcript" and capture.transcript is None:
        # Creerebbe un .md con transcript="" mai più ripreso da backfill/rescan (jobs._backfill
        # seleziona Capture.transcript.is_(None)): solo la trascrizione può nascere da zero.
        raise NotProcessed("la nota non è ancora stata elaborata")
    write_note(s, archive, capture, apply_edit(note_from_capture(capture), field, value), tz,
               vault=vault, public_url=public_url)
    s.commit()
    return capture


def reprocess(s: Session, capture_id: uuid.UUID, now: datetime) -> Job:
    """"Rielabora": di nuovo in coda da capo; i campi corretti a mano restano (spec AI §10)."""
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        raise InTrash("la registrazione è nel cestino")
    job = jobs.requeue(s, capture, now)
    s.commit()
    return job


def delete_capture(s: Session, archive: Archive, capture_id: uuid.UUID, *,
                   vault: Vault | None = None) -> None:
    capture = _get(s, capture_id)
    if capture.trashed_at is None:
        raise NotInTrash("si elimina definitivamente solo dal cestino")
    archive.delete(capture.rel_path)   # cancella anche il .md nel cestino
    if vault is not None and capture.note_path:
        vault.delete(capture.note_path)
    s.delete(capture)
    s.commit()


def purge_trash(s: Session, archive: Archive, now: datetime, retention_days: int, *,
                vault: Vault | None = None) -> int:
    limit = now - timedelta(days=retention_days)
    expired = s.scalars(select(Capture).where(Capture.trashed_at.is_not(None),
                                              Capture.trashed_at < limit)).all()
    for capture in expired:
        archive.delete(capture.rel_path)
        if vault is not None and capture.note_path:
            vault.delete(capture.note_path)
        s.delete(capture)
    s.commit()
    return len(expired)
