"""Modifiche fatte in Obsidian riprese dal vault (spec vault §7).

Il worker, quando e' libero, chiama `check` ogni minuto: i file di `Captures/` con data di
modifica diversa da quella a catalogo vengono riletti e le differenze (titolo, riassunto,
tag, corpo) applicate come correzioni a mano, quindi protette da "Rielabora". Un file
rinominato in Obsidian da' il titolo manuale. File senza `id`, con `id` sconosciuto,
illeggibili o di conflitto di Syncthing: segnalati e lasciati stare.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .archive import Archive
from .models import Capture
from .notefile import MAX_TITLE_LEN, NoteError, merge_edits, parse_note, parse_note_id, parse_note_title
from .notes import effective_title, lock_capture, note_from_capture, write_note
from .sidecar import capture_to_sidecar
from .vault import NOTE_SUFFIX, Vault, clean_title_for_name

log = logging.getLogger(__name__)
NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}\.\d{2} (.+)$")
_reported: dict[str, float] = {}   # problemi gia' nel log: {percorso: mtime}


@dataclass
class VaultReport:
    changed: int = 0
    problems: list[str] = field(default_factory=list)


def _problem(report: VaultReport, rel: str, mtime: float, what: str) -> None:
    report.problems.append(f"{rel}: {what}")
    if _reported.get(rel) != mtime:
        log.warning("vault: %s: %s", rel, what)
        _reported[rel] = mtime


def _title_in_name(rel: str) -> str:
    """La parte titolo del nome del file: dopo "AAAA-MM-GG HH.MM " se c'e', altrimenti tutto."""
    stem = rel.rsplit("/", 1)[-1].removesuffix(NOTE_SUFFIX)
    m = NAME_RE.match(stem)
    title = (m.group(1) if m else stem).strip()
    return title[:MAX_TITLE_LEN].strip()


def _find(s: Session, capture_id: str, rel: str) -> Capture | None:
    rows = s.scalars(select(Capture).where(Capture.capture_id == capture_id)).all()
    for capture in rows:
        if capture.note_path == rel:
            return capture
    live = [c for c in rows if c.trashed_at is None]
    return live[0] if len(live) == 1 else None


def _read(vault: Vault, rel: str):
    text = vault.read_text(rel)
    return parse_note_id(text), parse_note(text), parse_note_title(text)


def _apply(s: Session, vault: Vault, archive: Archive | None, rel: str, mtime: float,
           tz: ZoneInfo, public_url: str | None, report: VaultReport) -> None:
    try:
        capture_id, parsed, file_title = _read(vault, rel)
    except (OSError, UnicodeDecodeError, NoteError) as exc:
        _problem(report, rel, mtime, f"nota non leggibile ({exc})")
        return
    if not capture_id:
        _problem(report, rel, mtime, "senza id nel frontmatter: non e' una nota del backend")
        return
    found = _find(s, capture_id, rel)
    if found is None:
        _problem(report, rel, mtime, f"id {capture_id} sconosciuto al catalogo")
        return
    capture = lock_capture(s, found.id)
    if capture is None:
        return
    if capture.trashed_at is not None:
        _problem(report, rel, mtime, "la registrazione e' nel cestino: file ignorato")
        return
    # Sotto il lock il file puo' essere cambiato (UI o worker): si riparte dal suo stato attuale.
    current = vault.mtime(rel)
    if current is None:
        return
    if current != mtime:
        try:
            capture_id, parsed, file_title = _read(vault, rel)
        except (OSError, UnicodeDecodeError, NoteError) as exc:
            _problem(report, rel, current, f"nota non leggibile ({exc})")
            return
        mtime = current
    if rel == capture.note_path and mtime == capture.note_mtime:
        return   # gia' allineato (lo ha scritto il backend nel frattempo)
    changed = False
    shown = capture.title or capture.title_auto
    renamed = False
    if capture.note_path != rel:   # rinominato (o spostato) in Obsidian
        if capture.note_path and vault.mtime(capture.note_path) is not None:
            _problem(report, rel, mtime, f"doppione di {capture.note_path}: ignorato")
            return
        wanted = clean_title_for_name(effective_title(capture, tz))
        title_in_name = _title_in_name(rel)
        if title_in_name and title_in_name != wanted:
            capture.title = title_in_name
            renamed = True   # il nome scelto a mano vale piu' del title nel frontmatter
        capture.note_path = rel
        changed = True
    note, new_title, edits = merge_edits(parsed, None if renamed else file_title,
                                         note_from_capture(capture), shown)
    title_changed = renamed
    if new_title:
        capture.title = new_title
        title_changed = True
    changed = changed or edits
    if changed:
        write_note(s, archive, capture, note, tz, vault=vault, public_url=public_url)
        if title_changed and archive is not None:
            archive.write_sidecar(capture.rel_path, capture_to_sidecar(capture))   # il titolo manuale vive anche li' (rescan)
        report.changed += 1
        log.info("vault: %s: modifiche riprese da Obsidian", capture.note_path)
    else:
        capture.note_mtime = mtime


def check(sessionmaker, vault: Vault, tz: ZoneInfo, public_url: str | None,
          now: datetime, archive: Archive | None = None) -> VaultReport:
    report = VaultReport()
    for rel in vault.conflicts():
        _problem(report, rel, vault.mtime(rel) or 0.0, "file di conflitto di Syncthing: ignorato")
    with sessionmaker() as s:
        known = {path: mtime for path, mtime in s.execute(
            select(Capture.note_path, Capture.note_mtime).where(Capture.note_path.is_not(None)))}
        for rel, mtime in vault.list_notes():
            if rel in known and known[rel] == mtime:
                continue
            try:
                _apply(s, vault, archive, rel, mtime, tz, public_url, report)
                s.commit()
            except Exception as exc:  # noqa: BLE001 - un file non deve fermare gli altri
                s.rollback()
                _problem(report, rel, mtime, f"errore nel riprendere la nota ({type(exc).__name__}: {exc})")
    return report
