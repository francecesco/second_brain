# Note scritte dall'app — Piano di implementazione

> Esecuzione task per task, TDD. Le checkbox non si spuntano: lo stato si legge dai commit e
> dallo "Stato" della spec.

**Obiettivo:** scrivere una nota dall'app web (pagina `/new`) che segue la pipeline delle
registrazioni trascritte: arricchimento AI, archivio, "Da fare", ricerca, tag, vault.

**Architettura:** il testo è salvato come `<base>.txt` al posto del WAV, accanto al sidecar,
sotto un dispositivo "App" (tipo `web`). La nota nasce con la trascrizione già corretta a mano
(`edited: ["transcript"]`) e il lavoro in fase `enrich`. Il tipo si ricava dall'estensione di
`rel_path`. Una nota `.txt` arrivata in `transcribe` (dopo `rescan`) si ricostruisce dal `.txt`
nel worker, senza provider audio.

**Stack:** FastAPI, SQLAlchemy/Postgres, Jinja + htmx, pytest.

**Spec:** `docs/specs/2026-10-12-backend-note-scritte-design.md`

## Vincoli globali

- Test: `cd backend && docker compose -f docker-compose.test.yml up -d && .venv/bin/pytest -q`
  (561 verdi alla partenza, devono restare verdi).
- Limite del testo `MAX_TEXT_NOTE_CHARS = 20_000`; titolo provvisorio al massimo 60 caratteri.
- Dispositivo: tipo `web`, nome "App", id esadecimale di 12 caratteri.
- Niente migrazioni, niente campi nuovi nel sidecar.
- Commit in italiano con prefisso `backend:`/`docs:`, firmati solo dall'autore, nessun
  riferimento all'assistente.
- UI secondo `docs/stile-ui.md`; prima del commit della UI screenshot a 1280 e 390 px.
- Deploy sulla ZimaBoard solo dopo la prova dell'autore in locale.

## Punti da sorvegliare

- Testo con soli spazi o a capo: deve dare errore, non una nota vuota → test in Task 2.
- Fine riga `\r\n` dal browser (i form li mandano così): normalizzati, altrimenti
  `sha256` diverso e niente riconoscimento dei doppioni → test in Task 2.
- Una nota scritta spostata di data o messa nel cestino e ripristinata deve restare `.txt`
  (oggi `move` restituisce `.wav`): dopo, `rel_path` punterebbe a un file inesistente → test
  in Task 1.
- `rescan` non deve segnalare i `.txt` come "sidecar senza WAV" né perderli → test in Task 3.
- "Rielabora" su una nota scritta non deve mai chiamare la trascrizione audio → test in Task 3.

---

### Task 1: Archivio con sorgente `.wav` o `.txt`

**File:**
- Modifica: `backend/secondbrain/archive.py`, `backend/secondbrain/rescan.py` (messaggi)
- Test: `backend/tests/test_archive.py`, `backend/tests/test_rescan.py`

**Interfacce prodotte:**
- `archive.WAV_SUFFIX = ".wav"`, `archive.TEXT_SUFFIX = ".txt"`,
  `archive.SOURCE_SUFFIXES = (WAV_SUFFIX, TEXT_SUFFIX)`
- `Archive.commit_capture(tmp, dir_rel, base, sidecar, suffix=WAV_SUFFIX) -> str`
- `Archive.move(rel, dest_dir_rel) -> str` conserva l'estensione di `rel`
- `Archive.iter_sidecar_rels()` restituisce la sorgente esistente (`.wav`, altrimenti `.txt`;
  se nessuna esiste, `.wav`)
- `Archive.iter_sources_without_sidecar()` sostituisce `iter_wavs_without_sidecar`

- [ ] **Step 1: test che falliscono** in `test_archive.py`:

```python
def test_commit_capture_text(archive):
    tmp = archive.new_incoming()
    tmp.write_bytes("ciao".encode())
    rel = archive.commit_capture(tmp, DAY, "091530_app", {"a": 1}, suffix=".txt")
    assert rel == f"{DAY}/091530_app.txt"
    assert archive.abs(rel).read_text() == "ciao"
    assert archive.read_sidecar(rel) == {"a": 1}


def test_move_keeps_text_suffix(archive):
    put(archive, f"{DAY}/091530_app.txt")
    put(archive, f"{DAY}/091530_app.json", b"{}")
    rel = archive.move(f"{DAY}/091530_app.txt", ".trash/" + DAY)
    assert rel == f".trash/{DAY}/091530_app.txt"
    assert archive.abs(rel).is_file()


def test_iter_sidecar_rels_returns_existing_source(archive):
    put(archive, f"{DAY}/091530_dev.wav"); put(archive, f"{DAY}/091530_dev.json", b"{}")
    put(archive, f"{DAY}/101530_app.txt"); put(archive, f"{DAY}/101530_app.json", b"{}")
    put(archive, f"{DAY}/111530_dev.json", b"{}")     # sidecar orfano
    assert list(archive.iter_sidecar_rels()) == [
        f"{DAY}/091530_dev.wav", f"{DAY}/101530_app.txt", f"{DAY}/111530_dev.wav"]


def test_iter_sources_without_sidecar(archive):
    put(archive, f"{DAY}/091530_dev.wav")
    put(archive, f"{DAY}/101530_app.txt")
    put(archive, f"{DAY}/101530_app.ai.json", b"{}")
    assert list(archive.iter_sources_without_sidecar()) == [
        f"{DAY}/091530_dev.wav", f"{DAY}/101530_app.txt"]
```

Se il test esistente usa `iter_wavs_without_sidecar`, rinominarlo.

- [ ] **Step 2:** `.venv/bin/pytest tests/test_archive.py -q` → FAIL (`suffix` sconosciuto,
  `.wav` da `move`, metodo mancante).

- [ ] **Step 3: implementazione** in `archive.py`:

```python
WAV_SUFFIX = ".wav"
TEXT_SUFFIX = ".txt"        # nota scritta dall'app (spec note scritte §4): sostituisce il WAV
SOURCE_SUFFIXES = (WAV_SUFFIX, TEXT_SUFFIX)

    def commit_capture(self, tmp: Path, dir_rel: str, base: str, sidecar: dict,
                       suffix: str = WAV_SUFFIX) -> str:
        directory = self.abs(dir_rel)
        directory.mkdir(parents=True, exist_ok=True)
        rel = f"{dir_rel}/{base}{suffix}"
        self.write_sidecar(rel, sidecar)
        os.replace(tmp, directory / f"{base}{suffix}")
        _fsync_dir(directory)
        return rel

    # in move(): ultima riga
        return f"{dest_dir_rel}/{new_base}{src.suffix}"

    def iter_sidecar_rels(self) -> Iterator[str]:
        """Percorso relativo della sorgente (WAV o testo) per ogni sidecar `<base>.json`,
        cestino compreso; se non c'è nessuna sorgente, il `.wav` (rescan lo segnala)."""
        for path in sorted(self.root.rglob("*" + SIDECAR_SUFFIX)):
            rel = path.relative_to(self.root)
            if rel.parts[0] == INCOMING or not path.is_file() or not _is_base_file(path):
                continue
            source = next((rel.with_suffix(s) for s in SOURCE_SUFFIXES
                           if (self.root / rel.with_suffix(s)).is_file()), rel.with_suffix(WAV_SUFFIX))
            yield source.as_posix()

    def iter_sources_without_sidecar(self) -> Iterator[str]:
        for path in sorted(p for s in SOURCE_SUFFIXES for p in self.root.rglob("*" + s)):
            rel = path.relative_to(self.root)
            if rel.parts[0] == INCOMING or not _is_base_file(path):
                continue
            if not path.with_suffix(SIDECAR_SUFFIX).exists():
                yield rel.as_posix()
```

`sorted(...)` sui `Path` mescola `.wav` e `.txt` nell'ordine dei nomi, come vuole il test.
In `rescan.py`: `iter_sources_without_sidecar()`, messaggi "sidecar senza sorgente" e
"sorgente senza sidecar, non importata"; aggiornare le asserzioni di `test_rescan.py` che
cercano i vecchi testi.

- [ ] **Step 4:** `.venv/bin/pytest -q` → tutto verde.

- [ ] **Step 5: commit** `backend: archivio con sorgente WAV o testo`.

---

### Task 2: Creazione di una nota scritta (`textnotes.py`) e titolo provvisorio

**File:**
- Crea: `backend/secondbrain/textnotes.py`
- Modifica: `backend/secondbrain/notes.py` (`effective_title`)
- Test: `backend/tests/test_textnotes.py`

**Interfacce consumate:** `Archive.commit_capture(..., suffix=TEXT_SUFFIX)` (Task 1),
`notes.write_note`, `jobs.enqueue(..., stage=jobs.STAGE_ENRICH)`, `catalog.find_captures`,
`naming.*`, `sidecar.capture_to_sidecar`.

**Interfacce prodotte:**
- `MAX_TEXT_NOTE_CHARS = 20_000`, `TEXT_DEVICE_TYPE = "web"`, `TEXT_DEVICE_NAME = "App"`,
  `PROVISIONAL_TITLE_CHARS = 60`
- `class TextNoteError(ValueError)`: messaggio da mostrare all'utente
- `normalize_text(raw: str) -> str`: `\r\n`/`\r` → `\n`, strip; vuoto o troppo lungo → `TextNoteError`
- `is_text_capture(capture) -> bool`: `rel_path` finisce con `.txt`
- `provisional_title(text: str) -> str | None`
- `new_capture_id(now_utc: datetime) -> str`: `cap_AAAAMMGG_HHMMSS`
- `app_device(s, now) -> Device`: lo trova per tipo o lo crea
- `create_text_note(s, archive, raw, capture_id, now, *, tz, language, vault=None,
  public_url=None) -> tuple[Capture, bool]`: `(nota, creata)`, commit incluso
- `read_source(archive, capture) -> str`: testo del `.txt` normalizzato (usato nel Task 3)

- [ ] **Step 1: test che falliscono** in `tests/test_textnotes.py`:

```python
"""Note scritte dall'app (spec note scritte): creazione, doppioni, titolo provvisorio."""
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from secondbrain import jobs, textnotes
from secondbrain.archive import Archive
from secondbrain.models import Capture, Device, Job
from secondbrain.notes import effective_title
from secondbrain.vault import Vault
from tests.helpers import NOW

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260928_120000"


def create(db, settings, raw="Chiamare Costanza per Home Assistant", cid=CID, vault=True):
    return textnotes.create_text_note(
        db, Archive(settings.archive_dir), raw, cid, NOW, tz=ROME, language="it",
        vault=Vault(settings.vault_dir) if vault else None)


def test_normalize_text():
    assert textnotes.normalize_text("  riga 1\r\nriga 2\r\n ") == "riga 1\nriga 2"
    with pytest.raises(textnotes.TextNoteError):
        textnotes.normalize_text(" \r\n\t ")
    with pytest.raises(textnotes.TextNoteError):
        textnotes.normalize_text("x" * (textnotes.MAX_TEXT_NOTE_CHARS + 1))


def test_provisional_title():
    assert textnotes.provisional_title("\n\n  Comprare   il latte \naltro") == "Comprare il latte"
    long = "parola " * 20
    title = textnotes.provisional_title(long)
    assert len(title) <= textnotes.PROVISIONAL_TITLE_CHARS and title.endswith("…")
    assert not title[:-1].endswith(" ")
    assert textnotes.provisional_title("   ") is None


def test_new_capture_id():
    assert textnotes.new_capture_id(NOW) == "cap_20260928_120000"


def test_creates_files_catalog_note_and_enrich_job(db, settings):
    capture, created = create(db, settings)
    assert created
    archive = Archive(settings.archive_dir)
    assert capture.rel_path == "2026/09/28/140000_" + capture.device_id + ".txt"
    assert archive.abs(capture.rel_path).read_text() == "Chiamare Costanza per Home Assistant"
    assert archive.read_sidecar(capture.rel_path)["duration_s"] == 0
    assert (capture.transcript, capture.edited, capture.language) == (
        "Chiamare Costanza per Home Assistant", ["transcript"], "it")
    assert capture.duration_s == 0 and capture.date_estimated is False and capture.recorded_at == NOW
    assert "Chiamare Costanza" in Vault(settings.vault_dir).read_text(capture.note_path)
    job = db.get(Job, capture.id)
    assert (job.stage, job.status) == (jobs.STAGE_ENRICH, "queued")
    device = db.get(Device, capture.device_id)
    assert (device.type, device.name, device.token_hash) == ("web", "App", None)


def test_app_device_is_created_once(db, settings):
    a, _ = create(db, settings, cid="cap_20260928_120000")
    b, _ = create(db, settings, raw="altro", cid="cap_20260928_120001")
    assert a.device_id == b.device_id
    assert len(db.scalars(select(Device).where(Device.type == "web")).all()) == 1


def test_double_submit_returns_the_same_note(db, settings):
    a, created_a = create(db, settings, raw="ciao\r\nmondo")
    b, created_b = create(db, settings, raw="ciao\nmondo")
    assert (created_a, created_b) == (True, False) and a.id == b.id
    assert db.scalar(select(Capture).where(Capture.id != a.id)) is None


def test_same_id_different_text_is_a_new_note(db, settings):
    a, _ = create(db, settings, raw="uno")
    b, created = create(db, settings, raw="due")
    assert created and a.id != b.id and b.rel_path.endswith("_2.txt")


def test_invalid_capture_id_gets_a_fresh_one(db, settings):
    capture, _ = create(db, settings, cid="../../etc")
    assert capture.capture_id == "cap_20260928_120000"


def test_empty_text_creates_nothing(db, settings):
    with pytest.raises(textnotes.TextNoteError):
        create(db, settings, raw="  \n ")
    assert db.scalar(select(Capture)) is None
    assert not any(Archive(settings.archive_dir).root.rglob("*.txt"))


def test_failure_after_files_cleans_up(db, settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db giù")
    monkeypatch.setattr(jobs, "enqueue", boom)
    with pytest.raises(RuntimeError):
        create(db, settings)
    assert db.scalar(select(Capture)) is None
    root = Archive(settings.archive_dir).root
    assert not any(root.rglob("*.txt")) and not any(root.rglob("*.json"))
    assert not any(Vault(settings.vault_dir).root.rglob("*.md"))


def test_effective_title_uses_provisional_title_for_text_notes(db, settings):
    capture, _ = create(db, settings, raw="Comprare il latte\ne il pane", vault=False)
    assert effective_title(capture, ROME) == "Comprare il latte"
    capture.title_auto = "Spesa"
    assert effective_title(capture, ROME) == "Spesa"
```

Controllare il nome dell'attributo radice di `Vault` (`root`) prima di usarlo nei test.

- [ ] **Step 2:** `.venv/bin/pytest tests/test_textnotes.py -q` → FAIL (modulo mancante).

- [ ] **Step 3: implementazione** `backend/secondbrain/textnotes.py`:

```python
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

from sqlalchemy import select
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
    return capture.rel_path.endswith(TEXT_SUFFIX)


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
    tmp = archive.new_incoming()
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    return tmp


def create_text_note(s: Session, archive: Archive, raw: str, capture_id: str, now: datetime, *,
                     tz: ZoneInfo, language: str, vault: Vault | None = None,
                     public_url: str | None = None) -> tuple[Capture, bool]:
    """(nota, creata). Stesso id e stesso testo di una nota esistente: quella, senza crearne
    un'altra (doppio invio del form). Commit incluso; se qualcosa fallisce i file si tolgono."""
    text = normalize_text(raw)
    if not naming.is_valid_capture_id(capture_id):
        capture_id = new_capture_id(now)
    data = text.encode("utf-8")
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
                         Note(transcript=text, language=language, edited=("transcript",)),
                         tz, vault=vault, public_url=public_url)
        jobs.enqueue(s, capture.id, now, stage=jobs.STAGE_ENRICH)
        s.commit()
    except BaseException:
        tmp.unlink(missing_ok=True)
        note_path = capture.note_path
        s.rollback()
        _cleanup(s, archive, vault, capture.id, capture.rel_path, note_path)
        raise
    log.info("nota scritta %s (%d caratteri)", capture.rel_path, len(text))
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
```

In `notes.py`, `effective_title` (import locale per evitare il ciclo `textnotes` → `notes`):

```python
def effective_title(capture: Capture, tz: ZoneInfo) -> str:
    """Titolo mostrato: manuale, altrimenti automatico, altrimenti per una nota scritta
    l'inizio del testo, altrimenti il default per fascia."""
    if capture.title or capture.title_auto:
        return capture.title or capture.title_auto
    from .textnotes import is_text_capture, provisional_title
    if is_text_capture(capture) and capture.transcript:
        title = provisional_title(capture.transcript)
        if title:
            return title
    return default_title(capture.recorded_at, tz)
```

Verificare anche il placeholder del titolo in `_detail.html` (`c.title_auto or
default_title(...)`): sostituire con `display_title(c, tz)`.

- [ ] **Step 4:** `.venv/bin/pytest -q` → tutto verde.

- [ ] **Step 5: commit** `backend: creazione delle note scritte e titolo provvisorio`.

---

### Task 3: Worker e `rescan` per le note scritte

**File:**
- Modifica: `backend/secondbrain/worker.py` (`_begin`)
- Test: `backend/tests/test_textnotes.py` (sezione worker e rescan)

**Interfacce consumate:** `textnotes.create_text_note`, `textnotes.is_text_capture`,
`textnotes.read_source` (Task 2); `Worker` e fixture dei test del worker (`FakeProvider`,
`FakeFactory`, `configure_providers` da `tests/ai_fakes.py`).

- [ ] **Step 1: test che falliscono**, in coda a `tests/test_textnotes.py`:

```python
from secondbrain import library, settings_store as store
from secondbrain.catalog import make_sessionmaker
from secondbrain.rescan import rescan
from secondbrain.worker import Worker
from tests.ai_fakes import FakeFactory, FakeProvider, configure_providers


@pytest.fixture
def worker_env(db, settings, clock):
    box = store.SecretBox(settings.settings_key)
    configure_providers(db, box, NOW)
    groq, gemini = FakeProvider("groq"), FakeProvider("gemini")
    worker = Worker(sessionmaker=make_sessionmaker(db.get_bind()),
                    archive=Archive(settings.archive_dir), tz=ROME, box=box,
                    factory=FakeFactory(groq=groq, gemini=gemini), clock=clock,
                    vault=Vault(settings.vault_dir))
    return worker, groq


def test_worker_enriches_a_text_note_without_transcribing(db, settings, worker_env):
    worker, groq = worker_env
    capture, _ = create(db, settings)
    assert worker.run_once()
    db.expire_all()
    capture = db.get(Capture, capture.id)
    assert capture.transcript == "Chiamare Costanza per Home Assistant"
    assert capture.title_auto and capture.processed_at
    assert [c[0] for c in groq.calls] == ["enrich"]


def test_reprocess_never_transcribes_a_text_note(db, settings, worker_env):
    worker, groq = worker_env
    capture, _ = create(db, settings)
    worker.run_once()
    library.reprocess(db, capture.id, NOW)
    db.commit()
    worker.run_once()
    assert [c[0] for c in groq.calls] == ["enrich", "enrich"]


def test_rescan_without_note_rebuilds_text_through_the_worker(db, settings, worker_env):
    worker, groq = worker_env
    capture, _ = create(db, settings)
    vault = Vault(settings.vault_dir)
    vault.delete(capture.note_path)                   # nota persa
    db.delete(db.get(Job, capture.id)); db.commit()
    report = rescan(db, Archive(settings.archive_dir), NOW, vault=vault)
    db.commit()
    assert report.queued == 1 and not [p for p in report.problems if ".txt" in p]
    assert worker.run_once()
    db.expire_all()
    capture = db.get(Capture, capture.id)
    assert capture.transcript == "Chiamare Costanza per Home Assistant"
    assert capture.edited == ["transcript"] and capture.title_auto
    assert "transcribe" not in [c[0] for c in groq.calls]


def test_trash_and_restore_keep_the_text_source(db, settings):
    capture, _ = create(db, settings, vault=False)
    archive = Archive(settings.archive_dir)
    library.trash_capture(db, archive, capture.id, NOW)
    library.restore_capture(db, archive, capture.id)
    db.commit()
    assert db.get(Capture, capture.id).rel_path.endswith(".txt")
    assert archive.abs(db.get(Capture, capture.id).rel_path).is_file()
```

Controllare la forma di `FakeProvider.calls` (tupla con il nome della fase come primo
elemento, come in `test_worker.stages`) e le firme di `trash_capture`/`restore_capture`
(parametri del vault) e adattare.

- [ ] **Step 2:** `.venv/bin/pytest tests/test_textnotes.py -q` → FAIL sul test di `rescan`:
  il worker manda il `.txt` alla trascrizione audio.

- [ ] **Step 3: implementazione** in `worker.py`, `_begin`, subito dopo
  `stage, text, now, until = ...`:

```python
            if stage == jobs.STAGE_TRANSCRIBE and text is None and textnotes.is_text_capture(capture):
                # Nota scritta senza `.md` (es. dopo rescan): il testo è il `.txt`, niente
                # provider audio (spec note scritte §5).
                note = replace(notes.note_from_capture(capture),
                               transcript=textnotes.read_source(self.archive, capture),
                               edited=_edited((*capture.edited, "transcript")),
                               language=capture.language or store.get_language(s))
                notes.write_note(s, self.archive, capture, note, self.tz, vault=self.vault,
                                 public_url=self.public_url)
                text, stage = note.transcript, jobs.STAGE_ENRICH
                until = jobs.advance(s, lease.capture_id, stage, now).locked_until
```

Import: `from dataclasses import replace`, `from . import textnotes`,
`from .notefile import _edited` (già usato da `notes.py`). Verificare che i nomi degli attributi
del worker siano `self.vault` e `self.public_url`.

- [ ] **Step 4:** `.venv/bin/pytest -q` → tutto verde.

- [ ] **Step 5: commit** `backend: le note scritte nel worker e nel rescan`.

---

### Task 4: Pagina `/new`, pulsante "Nuova nota" e righe delle note scritte

**File:**
- Crea: `backend/secondbrain/web/compose.py`, `backend/secondbrain/web/templates/new.html`
- Modifica: `backend/secondbrain/app.py` (router), `web/browse.py` (audio 404),
  `web/templating.py` (global `is_text_capture`), template `finder.html`, `_row.html`,
  `_detail.html`, `trash.html`, `_icons.html` (icona `pencil`), `static/style.css`
- Test: `backend/tests/test_compose.py`

**Interfacce consumate:** `textnotes.create_text_note`, `TextNoteError`, `new_capture_id`,
`MAX_TEXT_NOTE_CHARS`, `is_text_capture`; `store.get_language`; `page_context` di
`web/context.py`.

- [ ] **Step 1: test che falliscono** in `tests/test_compose.py`:

```python
"""Pagina /new (spec note scritte §3)."""
from sqlalchemy import select

from secondbrain.models import Capture
from tests.helpers import capture_by


def test_new_page_has_form_and_capture_id(ui):
    r = ui.client.get("/new")
    assert r.status_code == 200
    assert 'name="text"' in r.text and "autofocus" in r.text
    assert 'name="capture_id" value="cap_20260928_120000"' in r.text


def test_header_has_new_note_button(ui):
    assert 'href="/new"' in ui.client.get("/todo").text


def test_save_redirects_to_todo_and_creates_note(ui, db):
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": "Comprare il latte",
                                     "capture_id": "cap_20260928_120000"}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/todo")
    page = ui.client.get("/todo").text
    assert "Comprare il latte" in page and "3 parole" in page
    assert "/audio" not in page


def test_empty_text_is_422_and_keeps_nothing(ui, db):
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": "   ",
                                     "capture_id": "cap_20260928_120000"})
    assert r.status_code == 422 and "La nota è vuota." in r.text
    assert db.scalar(select(Capture)) is None


def test_too_long_text_is_422_and_keeps_the_text(ui):
    text = "a" * 20_001
    r = ui.client.post("/new", data={"csrf": ui.csrf, "text": text, "capture_id": "cap_20260928_120000"})
    assert r.status_code == 422 and text in r.text


def test_csrf_required(ui):
    r = ui.client.post("/new", data={"csrf": "sbagliato", "text": "x"})
    assert r.status_code == 403


def test_login_required(lan_client):
    assert lan_client.get("/new", follow_redirects=False).status_code == 303


def test_detail_of_text_note_has_no_player_and_audio_is_404(ui, db):
    ui.client.post("/new", data={"csrf": ui.csrf, "text": "ciao", "capture_id": "cap_20260928_120000"})
    capture = capture_by(db, "cap_20260928_120000")
    assert "<audio" not in ui.client.get(f"/captures/{capture.id}").text
    assert ui.client.get(f"/captures/{capture.id}/audio").status_code == 404
```

- [ ] **Step 2:** `.venv/bin/pytest tests/test_compose.py -q` → FAIL (404 su `/new`).

- [ ] **Step 3: implementazione.**

`web/compose.py`:

```python
"""Nota scritta dall'app (spec note scritte §3): pagina /new e salvataggio."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import settings_store as store, textnotes
from ..models import WebSession
from .context import page_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

router = APIRouter()


def _page(request: Request, db: Session, session: WebSession, *, text: str = "",
          capture_id: str | None = None, error: str | None = None, status: int = 200):
    ctx = page_context(request, db, session, None) | {
        "crumbs": [("Nuova nota", None)], "text": text, "error": error,
        "capture_id": capture_id or textnotes.new_capture_id(request.app.state.clock()),
        "MAX_TEXT_NOTE_CHARS": textnotes.MAX_TEXT_NOTE_CHARS}
    return templates.TemplateResponse(request, "new.html", ctx, status_code=status)


@router.get("/new")
def new_page(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)):
    return _page(request, db, session)


@router.post("/new")
def save_note(request: Request, text: str = Form(""), capture_id: str = Form(""),
              db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    state = request.app.state
    try:
        textnotes.create_text_note(
            db, state.archive, text, capture_id, state.clock(), tz=state.settings.tz_archive,
            language=store.get_language(db), vault=state.vault, public_url=state.settings.public_url)
    except textnotes.TextNoteError as exc:
        return _page(request, db, session, text=text, capture_id=capture_id, error=str(exc), status=422)
    except OSError:
        return _page(request, db, session, text=text, capture_id=capture_id, status=500,
                     error="Salvataggio non riuscito (disco?): il testo è ancora qui, riprova.")
    return RedirectResponse("/todo", status_code=303)
```

Controllare la firma di `page_context` (dispositivo come quarto argomento posizionale, come in
`todo_page`). In `app.py`: `from .web import compose as web_compose` e
`app.include_router(web_compose.router)`.

`templates/new.html`:

```html
{% extends "finder.html" %}
{% from "_icons.html" import icon %}
{% block main %}
<form method="post" action="/new" class="compose" id="compose">
  <input type="hidden" name="csrf" value="{{ csrf }}">
  <input type="hidden" name="capture_id" value="{{ capture_id }}">
  <label for="compose-text" class="lab">Nuova nota</label>
  <textarea id="compose-text" name="text" rows="10" maxlength="{{ MAX_TEXT_NOTE_CHARS }}" autofocus required
            placeholder="Scrivi qui: titolo e tag li aggiunge l'elaborazione"{% if error %} aria-invalid="true" aria-describedby="compose-error"{% endif %}>{{ text }}</textarea>
  {% if error %}<p class="notice" role="alert" id="compose-error">{{ icon("fix") }}<span>{{ error }}</span></p>{% endif %}
  <div class="actions">
    <button type="submit" class="pri" aria-label="Salva" title="Salva (Ctrl+Invio)">{{ icon("save") }}<span class="label">Salva</span></button>
    <a href="/todo">Annulla</a>
  </div>
</form>
<script>
(function () {
  var form = document.getElementById("compose"), area = document.getElementById("compose-text"), KEY = "sb-draft";
  function get() { try { return localStorage.getItem(KEY); } catch (e) { return null; } }
  function set(v) { try { v ? localStorage.setItem(KEY, v) : localStorage.removeItem(KEY); } catch (e) {} }
  if (!area.value) { var d = get(); if (d) area.value = d; }
  area.addEventListener("input", function () { set(area.value); });
  area.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); form.requestSubmit(); }
  });
  form.addEventListener("submit", function () { form.querySelector("button[type=submit]").disabled = true; });
})();
</script>
{% endblock %}
```

La bozza si cancella al ritorno su `/todo` dopo un salvataggio: in `new.html` la bozza si
scrive a ogni `input`; per toglierla, nel `submit` si salva un flag
`sessionStorage.setItem("sb-draft-sent", "1")` e in `base.html` (una riga nello script
esistente) se il flag c'è e la pagina non è `/new` si tolgono bozza e flag. Così una
risposta 422 (che resta su `/new`) non perde la bozza. Il pulsante disabilitato al
`submit` evita il doppio tocco; il `capture_id` copre la rete lenta.

`finder.html`, nell'intestazione subito prima di `<details class="menu">`:

```html
  <a class="new-note{% if section == 'new' %} on{% endif %}" href="/new" aria-label="Nuova nota" title="Nuova nota">{{ icon("pencil") }}<span class="label">Nuova nota</span></a>
```

e `"new": "Nuova nota"` in `section_labels`.

`_icons.html`, nuovo ramo:

```html
{%- elif name == "pencil" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M4 20h4L19 9l-4-4L4 16Z"/><line x1="13.5" y1="6.5" x2="17.5" y2="10.5"/></svg>
```

`style.css`, vicino alle regole di `.top` (stessi token di `button.pri`):

```css
.top .new-note { display: inline-flex; align-items: center; gap: .4rem; min-height: 2.5rem; padding: 0 .8rem;
  background: var(--acc); color: var(--acc-fg); border: 1px solid var(--acc); border-radius: var(--r, 6px);
  box-shadow: 0 2px 0 var(--acc-edge); text-decoration: none; }
.top .new-note:hover { filter: brightness(1.08); }
.compose { display: grid; gap: .6rem; max-width: 48rem; }
.compose textarea { width: 100%; min-height: 12rem; resize: vertical; font: inherit; }
@media (max-width: 640px) { .top .new-note .label { display: none; } .top .new-note { padding: 0 .6rem; } }
```

Adattare raggio, altezza minima e breakpoint ai token e alle regole già in `style.css` (cercare
come `.top nav a .label` si nasconde a 390 px e usare lo stesso breakpoint).

`templating.py`: `templates.env.globals["is_text_capture"] = is_text_capture` (import da
`..textnotes`).

`_row.html`, riga `meta`:

```html
    <span class="meta">{{ device_names.get(c.device_id, c.device_id) }} · {% if is_text_capture(c) %}{{ (c.transcript or '')|wordcount }} parole{% else %}{{ c.duration_s|duration }} · {{ c.size_bytes|size }}{% endif %}</span>
```

`_detail.html`: `{% if not is_text_capture(c) %}<audio …></audio>{% endif %}`; placeholder del
titolo con `display_title(c, tz)` se non già fatto nel Task 2. `trash.html`: il link "Ascolta"
dentro `{% if not is_text_capture(c) %}`.

`web/browse.py`, `capture_audio`: dopo `_capture(...)`:

```python
    if is_text_capture(capture):
        raise HTTPException(404, "nota scritta, senza audio")
```

- [ ] **Step 4:** `.venv/bin/pytest -q` → tutto verde.

- [ ] **Step 5: screenshot** con Chrome headless (procedura di `docs/stile-ui.md`) a 1280 e
  390 px, chiaro e scuro: `/todo` con il pulsante e una nota scritta, `/new` vuota e con
  errore, dettaglio aperto di una nota scritta. Controllo contro la guida: un solo accento
  arancione per area, bersagli al tocco ≥ 40 px, niente scroll orizzontale a 390 px.

- [ ] **Step 6: commit** `backend: pagina Nuova nota e righe delle note scritte`.

---

### Task 5: Documentazione e prova in locale

**File:** `backend/README.md`, `docs/stile-ui.md`, spec (Stato), `CLAUDE.md`.

- [ ] **Step 1:** `backend/README.md`: sezione "Note scritte" (pulsante, limite, dispositivo
  "App", `.txt` nell'archivio, pipeline). `docs/stile-ui.md`: pulsante d'azione
  nell'intestazione (unico elemento arancione pieno della barra). Spec: "Stato" con data,
  numero di test e deviazioni. `CLAUDE.md`: voce nello storico e nello stato.
- [ ] **Step 2: commit** `docs: note scritte dall'app`.
- [ ] **Step 3: avvio in locale** `cd backend && docker compose up -d --build`, poi una nota
  scritta vera con le chiavi del Mac: titolo e tag arrivano, nota nel vault, ricerca la trova.
  Istruzioni di prova all'autore (PC e telefono in LAN su `http://<ip del Mac>:8000`).
- [ ] **Step 4:** merge e deploy sulla ZimaBoard **solo dopo l'ok dell'autore**.
