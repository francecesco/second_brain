"""Note scritte dall'app (spec note scritte §3-5): testo al posto del WAV, stessa pipeline.

Il testo è l'originale immutabile (`<base>.txt`); la nota nasce con la trascrizione già
"corretta a mano" e il lavoro in fase `enrich`, così il worker la arricchisce come una
registrazione trascritta e "Rielabora" non tocca il testo.
"""
import hashlib
import logging
import os
import secrets
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from . import catalog, jobs, naming, notes
from .archive import TEXT_SUFFIX, Archive
from .models import Capture, Device
from .notefile import Note
from .sidecar import capture_to_sidecar
from .vault import Vault

log = logging.getLogger(__name__)

MAX_TEXT_NOTE_CHARS = 20_000      # ~10 minuti di parlato, come la registrazione più lunga
TEXT_DEVICE_TYPE = "web"
TEXT_DEVICE_NAME = "App"
PROVISIONAL_TITLE_CHARS = 60
ELLIPSIS = "…"
CREATE_LOCK_KEY = 0x5B7E_0001   # advisory lock di transazione: una creazione alla volta


class TextNoteError(ValueError):
    """Testo non accettabile: il messaggio va mostrato all'utente."""


def normalize_text(raw: str) -> str:
    text = raw.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise TextNoteError("La nota è vuota.")
    if len(text) > MAX_TEXT_NOTE_CHARS:
        raise TextNoteError(f"La nota supera i {MAX_TEXT_NOTE_CHARS} caratteri.")
    return text


def is_text_capture(capture) -> bool:
    return (capture.rel_path or "").endswith(TEXT_SUFFIX)


def provisional_title(text: str) -> str | None:
    """Inizio della prima riga non vuota, tagliato su parola: solo visualizzazione."""
    for line in text.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if len(line) <= PROVISIONAL_TITLE_CHARS:
            return line
        cut = line[:PROVISIONAL_TITLE_CHARS - len(ELLIPSIS) + 1]
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut[:-1]
        return cut.rstrip() + ELLIPSIS
    return None


def new_capture_id(now_utc: datetime) -> str:
    return f"cap_{now_utc:%Y%m%d_%H%M%S}"


def app_device(s: Session, now: datetime) -> Device:
    device = s.scalars(select(Device).where(Device.type == TEXT_DEVICE_TYPE)
                       .order_by(Device.created_at).limit(1)).first()
    if device is None:
        device = Device(id=secrets.token_hex(6), name=TEXT_DEVICE_NAME, type=TEXT_DEVICE_TYPE,
                        token_hash=None, created_at=now)
        s.add(device)
        s.flush()
    return device


def read_source(archive: Archive, capture: Capture) -> str:
    raw = archive.abs(capture.rel_path).read_text(encoding="utf-8")
    return raw.replace("\r\n", "\n").replace("\r", "\n").strip()


def _write_incoming(archive: Archive, data: bytes):
    """Il testo in `.incoming/`; se la scrittura fallisce (disco pieno) il file parziale si
    toglie (spec note scritte §6)."""
    tmp = archive.new_incoming()
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp


def create_text_note(s: Session, archive: Archive, raw: str, capture_id: str, now: datetime, *,
                     tz: ZoneInfo, language: str, vault: Vault | None = None,
                     public_url: str | None = None) -> tuple[Capture, bool]:
    """(nota, creata). Stesso id e stesso testo di una nota esistente: quella, senza crearne
    un'altra (doppio invio del form). Commit incluso; se qualcosa fallisce i file si tolgono."""
    text_ = normalize_text(raw)
    # Serializza le creazioni (doppio invio quasi simultaneo, primo device "App"): la seconda
    # aspetta il commit della prima e poi vede la riga; il lock cade con commit/rollback.
    s.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CREATE_LOCK_KEY})
    if not naming.is_valid_capture_id(capture_id):
        capture_id = new_capture_id(now)
    data = text_.encode("utf-8")
    sha = hashlib.sha256(data).hexdigest()
    device = app_device(s, now)
    for previous in catalog.find_captures(s, device.id, capture_id):
        if previous.sha256 == sha:
            s.commit()   # il device "App" appena creato, se è il caso
            return previous, False
    day = naming.local_day(now, tz)
    dir_rel = naming.day_dir(day)
    base = archive.reserve_base(dir_rel, naming.base_name(now, tz, device.id))
    capture = Capture(
        id=uuid.uuid4(), device_id=device.id, capture_id=capture_id, recorded_at=now,
        date_estimated=False, received_at=now, day=day, rel_path=f"{dir_rel}/{base}{TEXT_SUFFIX}",
        title=None, duration_s=0.0, size_bytes=len(data), sha256=sha, firmware_version=None,
        battery_pct=None, battery_v=None, power_source=None, trashed_at=None, done_at=None)
    tmp = _write_incoming(archive, data)
    try:
        archive.commit_capture(tmp, dir_rel, base, capture_to_sidecar(capture), suffix=TEXT_SUFFIX)
        s.add(capture)
        s.flush()
        notes.write_note(s, archive, capture,
                         Note(transcript=text_, language=language, edited=("transcript",)),
                         tz, vault=vault, public_url=public_url)
        jobs.enqueue(s, capture.id, now, stage=jobs.STAGE_ENRICH)
        s.commit()
    except BaseException:
        tmp.unlink(missing_ok=True)
        note_path = capture.note_path
        s.rollback()
        _cleanup(s, archive, vault, capture.id, capture.rel_path, note_path)
        raise
    log.info("nota scritta %s (%d caratteri)", capture.rel_path, len(text_))
    return capture, True


def _cleanup(s: Session, archive: Archive, vault: Vault | None, capture_id: uuid.UUID,
             rel: str, note_path: str | None) -> None:
    """Come l'upload (ingest): i file si tolgono solo se la riga non c'è davvero; se non si
    può verificare restano e `rescan` li riconcilia."""
    try:
        exists = s.get(Capture, capture_id) is not None
    except Exception:
        log.exception("impossibile verificare se %s è stata salvata: i file restano", rel)
        return
    if exists:
        return
    archive.delete(rel)
    if vault is not None and note_path:
        vault.delete(note_path)
