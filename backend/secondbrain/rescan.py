"""Ricostruzione del catalogo dai sidecar su disco (spec §4, §6 "Recupero").

Il disco è la verità: posizione del file → rel_path, day e stato del cestino; sidecar →
tutto il resto; `<base>.md` → campi AI (spec AI §6). Righe senza file vengono tolte, file
senza sidecar solo segnalati; un `.md` illeggibile è segnalato e non cancella niente; una
nota fuori dal cestino senza `.md` va in coda con priorità bassa.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import jobs
from .archive import NOTE_SUFFIX, TRASH, Archive
from .models import Capture, Device
from .naming import DEFAULT_DEVICE_TYPE, parse_day_dir
from .notefile import NoteError, parse_note
from .notes import empty_ai_fields, lock_capture, note_fields
from .search import refresh_search_vector
from .sidecar import sidecar_to_fields, capture_to_sidecar


@dataclass
class RescanReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    queued: int = 0
    devices_created: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _sync_trashed_at(archive: Archive, rel_wav: str, sidecar_data: dict,
                     resolved_trashed_at: datetime | None) -> None:
    """Rewrite sidecar if trashed_at changed, keeping disk as truth."""
    sidecar_trashed_at = sidecar_data.get("trashed_at")
    resolved_iso = resolved_trashed_at.isoformat() if resolved_trashed_at else None
    if sidecar_trashed_at != resolved_iso:
        sidecar_data["trashed_at"] = resolved_iso
        archive.write_sidecar(rel_wav, sidecar_data)


def _load(archive: Archive, rel_wav: str, now: datetime, report: RescanReport) -> dict | None:
    if not archive.abs(rel_wav).is_file():
        report.problems.append(f"{rel_wav}: sidecar senza WAV")
        return None
    in_trash = rel_wav.startswith(TRASH + "/")
    try:
        sidecar_data = archive.read_sidecar(rel_wav)
        fields = sidecar_to_fields(sidecar_data)
        day = parse_day_dir(rel_wav.removeprefix(TRASH + "/").rsplit("/", 1)[0])
    except (ValueError, KeyError, TypeError) as exc:
        report.problems.append(f"{rel_wav}: sidecar non leggibile ({exc})")
        return None
    resolved_trashed_at = (fields["trashed_at"] or now) if in_trash else None
    _sync_trashed_at(archive, rel_wav, sidecar_data, resolved_trashed_at)
    fields["trashed_at"] = resolved_trashed_at
    return fields | {"rel_path": rel_wav, "day": day}


def _note_fields(archive: Archive, rel_wav: str, report: RescanReport) -> tuple[dict | None, bool]:
    """(campi AI dal `.md`, oppure None se è illeggibile e il catalogo resta com'è; manca il `.md`)."""
    try:
        text = archive.read_text(rel_wav, NOTE_SUFFIX)
    except FileNotFoundError:
        return empty_ai_fields(), True
    except (OSError, UnicodeDecodeError) as exc:
        report.problems.append(f"{rel_wav}: nota .md non leggibile ({exc})")
        return None, False
    try:
        return note_fields(parse_note(text)), False
    except NoteError as exc:
        report.problems.append(f"{rel_wav}: nota .md non leggibile ({exc})")
        return None, False


def _ensure_device(s: Session, device_id: str, now: datetime, report: RescanReport) -> None:
    if s.get(Device, device_id) is None:
        s.add(Device(id=device_id, name=device_id, type=DEFAULT_DEVICE_TYPE,
                     token_hash=None, created_at=now))
        s.flush()
        report.devices_created.append(device_id)


def _restore_from_catalog(archive: Archive, capture: Capture, report: RescanReport) -> dict:
    """Il sidecar è illeggibile o sparito ma il WAV c'è ancora: il catalogo ha comunque
    titolo, data corretta, device e capture_id, quindi si riscrive il sidecar da lì
    invece di buttare via la riga."""
    data = capture_to_sidecar(capture)
    archive.write_sidecar(capture.rel_path, data)
    report.problems.append(f"{capture.rel_path}: sidecar ricostruito dal catalogo")
    return sidecar_to_fields(data) | {"rel_path": capture.rel_path, "day": capture.day}


def rescan(s: Session, archive: Archive, now: datetime) -> RescanReport:
    report = RescanReport()
    entries: dict[uuid.UUID, dict] = {}
    for rel_wav in archive.iter_sidecar_rels():
        values = _load(archive, rel_wav, now, report)
        if values is None:
            continue
        if values["id"] in entries:
            report.problems.append(
                f"{rel_wav}: id {values['id']} già usato da {entries[values['id']]['rel_path']}")
            continue
        entries[values["id"]] = values

    # Un rel_path già rivendicato da un sidecar valido (con un id diverso, es. modificato
    # a mano) non va mai ricostruito dal catalogo: vince il sidecar, come per ogni altro
    # sidecar valido.
    claimed_rel_paths = {values["rel_path"] for values in entries.values()}

    # Righe la cui riscrittura è fallita sopra (sidecar illeggibile o sparito): se il WAV
    # esiste ancora e nessun sidecar valido rivendica la stessa posizione, si ricostruisce
    # il sidecar dal catalogo; altrimenti (WAV sparito, o la posizione è rivendicata da un
    # sidecar valido con un altro id) si toglie la riga. Va fatto prima di segnalare i WAV
    # senza sidecar, così uno appena ricostruito non viene più segnalato come mancante
    # nello stesso giro.
    for capture in s.scalars(select(Capture)).all():
        if capture.id in entries:
            continue
        if capture.rel_path not in claimed_rel_paths and archive.abs(capture.rel_path).is_file():
            entries[capture.id] = _restore_from_catalog(archive, capture, report)
        else:
            s.delete(capture)
            report.removed += 1
    s.flush()

    for rel_wav in archive.iter_wavs_without_sidecar():
        report.problems.append(f"{rel_wav}: WAV senza sidecar, non importato")

    to_queue: list[uuid.UUID] = []
    for values in entries.values():
        _ensure_device(s, values["device_id"], now, report)
        # Riga già esistente: prima il lock (`notes.lock_capture`, lo stesso che tengono UI e
        # worker mentre scrivono i campi AI), poi la lettura del `.md`. Letto prima, un `.md`
        # scritto dal worker nel frattempo verrebbe sovrascritto con i campi vuoti.
        capture = lock_capture(s, values["id"])
        ai_fields, missing_note = _note_fields(archive, values["rel_path"], report)
        if ai_fields is not None:
            values.update(ai_fields)
        if missing_note and values["trashed_at"] is None:
            to_queue.append(values["id"])
        if capture is None:
            s.add(Capture(**values))
            report.added += 1
            continue
        changed = [key for key, value in values.items() if getattr(capture, key) != value]
        for key in changed:
            setattr(capture, key, values[key])
        if changed:
            report.updated += 1
    s.flush()

    for capture_id in entries:
        refresh_search_vector(s, s.get(Capture, capture_id))
    for capture_id in to_queue:
        # `enqueue_if_idle` tiene il lock sulla riga del lavoro (dopo quello sulla riga
        # della registrazione, sopra) e lascia stare una nota già in coda o che il worker
        # sta elaborando proprio ora, invece di rimetterla in coda da capo.
        if jobs.enqueue_if_idle(s, capture_id, now, priority=jobs.PRIORITY_LOW) is not None:
            report.queued += 1
    return report
