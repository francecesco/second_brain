"""Modifiche fatte in Obsidian riprese dal vault (spec vault §7).

Il worker, quando e' libero, chiama `check` ogni minuto: i file di `Captures/` con data di
modifica diversa da quella a catalogo vengono riletti e le differenze (titolo, riassunto,
tag, corpo) applicate come correzioni a mano, quindi protette da "Rielabora". Un file
rinominato in Obsidian da' il titolo manuale. File senza `id`, con `id` sconosciuto,
illeggibili o di conflitto di Syncthing: segnalati e lasciati stare.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Capture
from .notefile import NoteError, apply_edit, parse_note, parse_note_id
from .notes import effective_title, lock_capture, note_from_capture, write_note
from .vault import NOTE_SUFFIX, Vault, clean_title_for_name

log = logging.getLogger(__name__)
NAME_PREFIX_LEN = len("2026-10-07 11.58 ")
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
    name = rel.rsplit("/", 1)[-1].removesuffix(NOTE_SUFFIX)
    return name[NAME_PREFIX_LEN:].strip()


def _find(s: Session, capture_id: str, rel: str) -> Capture | None:
    rows = s.scalars(select(Capture).where(Capture.capture_id == capture_id)).all()
    for capture in rows:
        if capture.note_path == rel:
            return capture
    live = [c for c in rows if c.trashed_at is None]
    return live[0] if len(live) == 1 else None


def _apply(s: Session, vault: Vault, rel: str, mtime: float, tz: ZoneInfo,
           public_url: str | None, report: VaultReport) -> None:
    try:
        text = vault.read_text(rel)
        capture_id = parse_note_id(text)
        parsed = parse_note(text)
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
    note = note_from_capture(capture)
    if parsed.title and parsed.title != shown and not renamed:
        capture.title = parsed.title
        changed = True
    if parsed.transcript != (capture.transcript or "").strip():
        note = apply_edit(note, "transcript", parsed.transcript)
        changed = True
    if (parsed.summary or None) != (capture.summary or None):
        note = apply_edit(note, "summary", parsed.summary or "")
        changed = True
    if tuple(parsed.tags) != tuple(capture.tags or ()):
        note = apply_edit(note, "tags", ", ".join(parsed.tags))
        changed = True
    if changed:
        write_note(s, None, capture, note, tz, vault=vault, public_url=public_url)
        report.changed += 1
        log.info("vault: %s: modifiche riprese da Obsidian", capture.note_path)
    else:
        capture.note_mtime = mtime


def check(sessionmaker, vault: Vault, tz: ZoneInfo, public_url: str | None,
          now: datetime) -> VaultReport:
    report = VaultReport()
    for rel in vault.conflicts():
        _problem(report, rel, vault.mtime(rel) or 0.0, "file di conflitto di Syncthing: ignorato")
    with sessionmaker() as s:
        known = {path: mtime for path, mtime in s.execute(
            select(Capture.note_path, Capture.note_mtime).where(Capture.note_path.is_not(None)))}
        for rel, mtime in vault.list_notes():
            if rel in known and known[rel] == mtime:
                continue
            _apply(s, vault, rel, mtime, tz, public_url, report)
            s.commit()
    return report
