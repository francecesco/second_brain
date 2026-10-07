"""Campi AI di una nota: `.md` sul disco e colonne del catalogo, scritti insieme (spec §6, §10).

UI e worker scrivono solo tenendo il lock sulla riga (`lock_capture`) e partendo dai
valori riletti sotto quel lock: così una correzione a mano non viene mai sovrascritta
da un'elaborazione in corso.
"""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .archive import NOTE_SUFFIX, Archive
from .models import Capture
from .notefile import Note, NoteMeta, render_note
from .search import refresh_search_vector
from .vault import Vault, note_dir, vault_note_name

MATTINO_START_HOUR = 5
POMERIGGIO_START_HOUR = 12
SERA_START_HOUR = 18
NOTTE_START_HOUR = 23


def default_title(recorded_at_utc: datetime, tz: ZoneInfo) -> str:
    """Titolo mostrato quando la nota non ne ha uno: solo visualizzazione e nome del file, mai salvato."""
    local = recorded_at_utc.astimezone(tz)
    hour = local.hour
    time_str = local.strftime("%H:%M")
    if MATTINO_START_HOUR <= hour < POMERIGGIO_START_HOUR:
        band = "del mattino"
    elif POMERIGGIO_START_HOUR <= hour < SERA_START_HOUR:
        band = "del pomeriggio"
    elif SERA_START_HOUR <= hour < NOTTE_START_HOUR:
        band = "della sera"
    else:
        band = "della notte"
    return f"Nota {band}, {time_str}"


def effective_title(capture: Capture, tz: ZoneInfo) -> str:
    """Titolo mostrato: manuale, altrimenti automatico, altrimenti il default per fascia."""
    return capture.title or capture.title_auto or default_title(capture.recorded_at, tz)


def note_meta(capture: Capture, tz: ZoneInfo, public_url: str | None) -> NoteMeta:
    return NoteMeta(capture_id=capture.capture_id, device_id=capture.device_id,
                    created=capture.recorded_at, duration_s=capture.duration_s,
                    title=capture.title or capture.title_auto,
                    url=f"{public_url}/captures/{capture.id}" if public_url else None)

def empty_ai_fields() -> dict:
    """Campi AI del catalogo di una nota senza `.md` (mai elaborata, o `.md` cancellato).

    Un dizionario nuovo a ogni chiamata, con liste nuove: nessuna riga ne condivide un'altra.
    """
    return {
        "transcript": None, "title_auto": None, "summary": None, "tags": [], "edited": [],
        "language": None, "ai_provider": None, "ai_transcribe_model": None,
        "ai_enrich_provider": None, "ai_enrich_model": None, "processed_at": None,
    }


def lock_capture(s: Session, capture_id: uuid.UUID) -> Capture | None:
    """Rilegge la riga e la tiene bloccata fino al commit o al rollback."""
    return s.get(Capture, capture_id, with_for_update=True, populate_existing=True)


def note_from_capture(capture: Capture) -> Note:
    return Note(
        transcript=capture.transcript or "", title=capture.title_auto, summary=capture.summary,
        tags=tuple(capture.tags or ()), language=capture.language, provider=capture.ai_provider,
        transcribe_model=capture.ai_transcribe_model, enrich_provider=capture.ai_enrich_provider,
        enrich_model=capture.ai_enrich_model, processed_at=capture.processed_at,
        edited=tuple(capture.edited or ()))


def note_fields(note: Note) -> dict:
    """Colonne del catalogo corrispondenti a una Note."""
    return {
        "transcript": note.transcript, "title_auto": note.title, "summary": note.summary,
        "tags": list(note.tags), "edited": list(note.edited), "language": note.language,
        "ai_provider": note.provider, "ai_transcribe_model": note.transcribe_model,
        "ai_enrich_provider": note.enrich_provider, "ai_enrich_model": note.enrich_model,
        "processed_at": note.processed_at,
    }


def expected_note_rel(vault: Vault, capture: Capture, tz: ZoneInfo) -> str:
    """Percorso atteso della nota nel vault (spec vault §5); nuovo nome solo se libero."""
    local = capture.recorded_at.astimezone(tz)
    name = vault_note_name(local, effective_title(capture, tz))
    wanted = f"{note_dir(local)}/{name}"
    if capture.note_path == wanted:
        return wanted
    return vault.unique_rel(note_dir(local), name)


def _place(vault: Vault, capture: Capture, note: Note, tz: ZoneInfo, public_url: str | None) -> None:
    wanted = expected_note_rel(vault, capture, tz)
    if capture.note_path and capture.note_path != wanted and vault.mtime(capture.note_path) is not None:
        vault.rename(capture.note_path, wanted)
    capture.note_path = wanted
    capture.note_mtime = vault.write_text(wanted, render_note(note, tz, note_meta(capture, tz, public_url)))


def write_note(s: Session, archive: Archive, capture: Capture, note: Note, tz: ZoneInfo, *,
               vault: Vault | None = None, public_url: str | None = None) -> None:
    """Prima il `.md` (atomico: nel vault, rinominato se il titolo e' cambiato; senza vault
    accanto al WAV come prima), poi catalogo e indice di ricerca; il commit lo fa chi chiama."""
    for key, value in note_fields(note).items():
        setattr(capture, key, value)
    if vault is not None:
        _place(vault, capture, note, tz, public_url)
    else:
        archive.write_text(capture.rel_path, NOTE_SUFFIX, render_note(note, tz))
    refresh_search_vector(s, capture)


def place_note(vault: Vault, capture: Capture, tz: ZoneInfo, public_url: str | None) -> None:
    """Titolo manuale o data cambiati: stesso contenuto AI, nome e frontmatter aggiornati.
    Una nota mai elaborata e senza correzioni non ha file: non se ne crea uno vuoto."""
    if capture.trashed_at is not None or (capture.transcript is None and not capture.note_path):
        return
    _place(vault, capture, note_from_capture(capture), tz, public_url)
