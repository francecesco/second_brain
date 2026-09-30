"""Campi AI di una nota: `.md` sul disco e colonne del catalogo, scritti insieme (spec §6, §10).

UI e worker scrivono solo tenendo il lock sulla riga (`lock_capture`) e partendo dai
valori riletti sotto quel lock: così una correzione a mano non viene mai sovrascritta
da un'elaborazione in corso.
"""
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from .archive import NOTE_SUFFIX, Archive
from .models import Capture
from .notefile import Note, render_note
from .search import refresh_search_vector

# Campi AI del catalogo di una nota senza `.md` (mai elaborata, o `.md` cancellato).
EMPTY_AI_FIELDS = {
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


def write_note(s: Session, archive: Archive, capture: Capture, note: Note, tz: ZoneInfo) -> None:
    """Prima il `.md` (atomico), poi catalogo e indice di ricerca; il commit lo fa chi chiama."""
    archive.write_text(capture.rel_path, NOTE_SUFFIX, render_note(note, tz))
    for key, value in note_fields(note).items():
        setattr(capture, key, value)
    refresh_search_vector(s, capture)
