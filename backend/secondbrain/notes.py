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
import re

from dataclasses import replace

from .notefile import (Note, NoteError, NoteMeta, _edited, apply_edit, parse_note, parse_note_title,
                       render_note)
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
    directory = note_dir(local)
    wanted = f"{directory}/{name}"
    if capture.note_path == wanted:
        return wanted
    if capture.note_path and capture.note_path.rsplit("/", 1)[0] == directory:
        # Il proprio file con suffisso di collisione "(k)" e' gia' il nome giusto: niente rinomine a vuoto.
        own = capture.note_path.rsplit("/", 1)[1]
        if re.fullmatch(re.escape(name[:-3]) + r"( \(\d+\))?\.md", own):
            return capture.note_path
    return vault.unique_rel(directory, name)


def _merge_unseen_edit(vault: Vault, capture: Capture, note: Note, prior: Note,
                       prior_title: str | None) -> Note:
    """Il file nel vault e' cambiato dopo l'ultima lettura del backend (Obsidian, tra un
    controllo e l'altro): i campi che l'utente ha cambiato rispetto a quanto il backend aveva
    scritto (`prior`) entrano nella nota nuova come correzioni a mano, invece di perdersi
    (spec vault §7, revisione)."""
    if not capture.note_path:
        return note
    mtime = vault.mtime(capture.note_path)
    if mtime is None or mtime == capture.note_mtime:
        return note
    try:
        text = vault.read_text(capture.note_path)
        file_note, file_title = parse_note(text), parse_note_title(text)
    except (OSError, UnicodeDecodeError, NoteError):
        return note   # illeggibile: vince il catalogo, il file viene riscritto
    if file_note.transcript != prior.transcript.strip():
        note = apply_edit(note, "transcript", file_note.transcript)
    if (file_note.summary or None) != (prior.summary or None):
        note = apply_edit(note, "summary", file_note.summary or "")
    if tuple(file_note.tags) != tuple(prior.tags):
        note = replace(note, tags=tuple(file_note.tags), edited=_edited((*note.edited, "tags")))
    if file_title and file_title != prior_title:
        capture.title = file_title
    return note


def _place(vault: Vault, capture: Capture, note: Note, tz: ZoneInfo, public_url: str | None,
           prior: Note | None = None, prior_title: str | None = None) -> Note:
    """Scrive la nota nel vault: fonde le modifiche non ancora viste, rinomina se il nome
    atteso e' cambiato, non riscrive un file identico. Ritorna la nota effettivamente scritta."""
    if prior is None:
        prior, prior_title = note_from_capture(capture), capture.title or capture.title_auto
    note = _merge_unseen_edit(vault, capture, note, prior, prior_title)
    for key, value in note_fields(note).items():
        setattr(capture, key, value)   # il nome atteso dipende anche dal titolo automatico
    wanted = expected_note_rel(vault, capture, tz)
    rendered = render_note(note, tz, note_meta(capture, tz, public_url))
    if capture.note_path and capture.note_path != wanted and vault.mtime(capture.note_path) is not None:
        vault.rename(capture.note_path, wanted)
    capture.note_path = wanted
    current = vault.mtime(wanted)
    if current is not None:
        try:
            if vault.read_text(wanted) == rendered:
                capture.note_mtime = current
                return note
        except (OSError, UnicodeDecodeError):
            pass
    capture.note_mtime = vault.write_text(wanted, rendered)
    return note


def write_note(s: Session, archive: Archive, capture: Capture, note: Note, tz: ZoneInfo, *,
               vault: Vault | None = None, public_url: str | None = None) -> None:
    """Prima il `.md` (atomico: nel vault, rinominato se il titolo e' cambiato; senza vault
    accanto al WAV come prima), poi catalogo e indice di ricerca; il commit lo fa chi chiama."""
    prior, prior_title = note_from_capture(capture), capture.title or capture.title_auto
    for key, value in note_fields(note).items():
        setattr(capture, key, value)
    if vault is not None:
        _place(vault, capture, note, tz, public_url, prior, prior_title)
    else:
        archive.write_text(capture.rel_path, NOTE_SUFFIX, render_note(note, tz))
    refresh_search_vector(s, capture)


def place_note(vault: Vault, capture: Capture, tz: ZoneInfo, public_url: str | None) -> None:
    """Titolo manuale o data cambiati: stesso contenuto AI, nome e frontmatter aggiornati.
    Una nota mai elaborata e senza correzioni non ha file: non se ne crea uno vuoto."""
    if capture.trashed_at is not None or (capture.transcript is None and not capture.note_path):
        return
    _place(vault, capture, note_from_capture(capture), tz, public_url)
