"""Ricostruzione del catalogo dai sidecar su disco (spec §4, §6 "Recupero").

Il disco è la verità: posizione del file → rel_path, day e stato del cestino; sidecar →
tutto il resto. Righe senza file vengono tolte, file senza sidecar solo segnalati.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .archive import TRASH, Archive
from .models import Capture, Device
from .naming import DEFAULT_DEVICE_TYPE, parse_day_dir
from .sidecar import sidecar_to_fields


@dataclass
class RescanReport:
    added: int = 0
    updated: int = 0
    removed: int = 0
    devices_created: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _load(archive: Archive, rel_wav: str, now: datetime, report: RescanReport) -> dict | None:
    if not archive.abs(rel_wav).is_file():
        report.problems.append(f"{rel_wav}: sidecar senza WAV")
        return None
    in_trash = rel_wav.startswith(TRASH + "/")
    try:
        fields = sidecar_to_fields(archive.read_sidecar(rel_wav))
        day = parse_day_dir(rel_wav.removeprefix(TRASH + "/").rsplit("/", 1)[0])
    except (ValueError, KeyError, TypeError) as exc:
        report.problems.append(f"{rel_wav}: sidecar non leggibile ({exc})")
        return None
    fields["trashed_at"] = (fields["trashed_at"] or now) if in_trash else None
    return fields | {"rel_path": rel_wav, "day": day}


def _ensure_device(s: Session, device_id: str, now: datetime, report: RescanReport) -> None:
    if s.get(Device, device_id) is None:
        s.add(Device(id=device_id, name=device_id, type=DEFAULT_DEVICE_TYPE,
                     token_hash=None, created_at=now))
        s.flush()
        report.devices_created.append(device_id)


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
    for rel_wav in archive.iter_wavs_without_sidecar():
        report.problems.append(f"{rel_wav}: WAV senza sidecar, non importato")

    # Prima si tolgono le righe senza file, così i loro rel_path tornano liberi.
    for capture in s.scalars(select(Capture)).all():
        if capture.id not in entries:
            s.delete(capture)
            report.removed += 1
    s.flush()

    for values in entries.values():
        _ensure_device(s, values["device_id"], now, report)
        capture = s.get(Capture, values["id"])
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
    return report
