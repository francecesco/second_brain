# Second Brain — Backend `secondbrain` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Costruire il servizio backend `secondbrain` che riceve audio dal device, lo trascrive in locale, scrive ogni nota come file Markdown in un vault Obsidian (fonte di verità), la indicizza in SQLite/FTS5, rende un digest 200×200 per l'e-Paper, e coinvolge il PiAgent on-demand.

**Architecture:** Servizio Python/FastAPI containerizzato su ZimaBoard. Il vault Obsidian (cartella di file `.md`) è la fonte di verità; SQLite/FTS5 è un indice derivato ricostruibile. STT locale con faster-whisper. Componenti a responsabilità singola (vault, index, stt, enrich, digest, watcher) orchestrati da `app.py`. Nessun audio lascia la rete di casa.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, python-frontmatter, faster-whisper, Pillow, httpx, SQLite (stdlib), pytest.

**Spec:** `docs/specs/2026-09-14-second-brain-design.md`

## Global Constraints

- Python **3.11+** (usa `str | None`, `list[str]`).
- Package Python: **`secondbrain`**. Prefisso env var: **`SECONDBRAIN_`**.
- **Vault = fonte di verità**; SQLite = indice **derivato** (mai l'unica copia del contenuto).
- **Nessun audio verso il cloud**: STT sempre locale (faster-whisper).
- Le catture del device creano sempre file **nuovi** (nessun edit di file esistenti → nessun conflitto).
- Scritture su file **atomiche** (write su tmp + `os.replace`).
- Formato audio atteso dal device: **WAV 16 kHz mono**.
- Digest: **BMP 200×200 a 1 bit** (modo Pillow `"1"`).
- Layout vault: cartelle `Captures/`, `Daily/`, `_audio/`.
- Git: identità **locale** `francecesco <francecesco78@gmail.com>` (già configurata nel repo). **Nessuna attribuzione all'assistente** nei commit.
- Tutto il codice backend vive in `second_brain/backend/`.

---

## File Structure

```
backend/
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── secondbrain/
│   ├── __init__.py
│   ├── config.py        # Config da env var
│   ├── models.py        # Capture, IndexedNote, Enrichment, DigestData (dataclass)
│   ├── vault.py         # slug, render markdown+frontmatter, scrittura atomica capture/daily
│   ├── index.py         # SQLite/FTS5: init, upsert, search, idempotenza, reindex
│   ├── stt.py           # Transcriber (Protocol), FakeTranscriber, WhisperTranscriber
│   ├── enrich.py        # Enricher (Protocol), NullEnricher, PiAgentEnricher
│   ├── digest.py        # render_digest(DigestData) -> BMP 200x200 1-bit
│   ├── watcher.py       # reindex CLI + file watcher
│   └── app.py           # create_app(config, transcriber, enricher) -> FastAPI
└── tests/
    ├── conftest.py
    ├── test_vault.py
    ├── test_index.py
    ├── test_stt.py
    ├── test_captures_api.py
    ├── test_notes_api.py
    ├── test_digest.py
    └── test_enrich.py
```

Responsabilità: ogni modulo è puro e testabile in isolamento; `app.py` è l'unico che li mette insieme. `stt`/`enrich` espongono un `Protocol` + una fake, così l'API si testa senza modelli pesanti né rete.

---

### Task 1: Scaffold, dipendenze e config

**Files:**
- Create: `backend/requirements.txt`
- Create: `backend/secondbrain/__init__.py` (vuoto)
- Create: `backend/secondbrain/config.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Consumes: —
- Produces: `Config` dataclass con campi `vault_dir: Path`, `db_path: Path`, `whisper_model: str`, `piagent_url: str | None`, `token: str | None`, `tz: str`; classmethod `Config.from_env(env: Mapping[str,str]) -> Config`.

- [ ] **Step 1: Scrivi `requirements.txt`**

```
fastapi==0.115.*
uvicorn[standard]==0.30.*
python-multipart==0.0.*
python-frontmatter==1.1.*
faster-whisper==1.0.*
Pillow==10.*
httpx==0.27.*
pytest==8.*
```

- [ ] **Step 2: Scrivi il test che fallisce** (`tests/test_config.py`)

```python
from pathlib import Path
from secondbrain.config import Config

def test_from_env_reads_values():
    cfg = Config.from_env({
        "SECONDBRAIN_VAULT_DIR": "/data/vault",
        "SECONDBRAIN_DB_PATH": "/data/index.db",
        "SECONDBRAIN_WHISPER_MODEL": "small",
        "SECONDBRAIN_PIAGENT_URL": "http://piagent:8080",
        "SECONDBRAIN_TOKEN": "secret",
        "SECONDBRAIN_TZ": "Europe/Rome",
    })
    assert cfg.vault_dir == Path("/data/vault")
    assert cfg.db_path == Path("/data/index.db")
    assert cfg.whisper_model == "small"
    assert cfg.piagent_url == "http://piagent:8080"
    assert cfg.token == "secret"
    assert cfg.tz == "Europe/Rome"

def test_from_env_defaults():
    cfg = Config.from_env({"SECONDBRAIN_VAULT_DIR": "/v", "SECONDBRAIN_DB_PATH": "/db"})
    assert cfg.whisper_model == "small"
    assert cfg.piagent_url is None
    assert cfg.tz == "Europe/Rome"
```

- [ ] **Step 3: Verifica che fallisca**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: FAIL con `ModuleNotFoundError: secondbrain.config`

- [ ] **Step 4: Implementa `config.py`**

```python
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

@dataclass(frozen=True)
class Config:
    vault_dir: Path
    db_path: Path
    whisper_model: str = "small"
    piagent_url: str | None = None
    token: str | None = None
    tz: str = "Europe/Rome"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Config":
        return cls(
            vault_dir=Path(env["SECONDBRAIN_VAULT_DIR"]),
            db_path=Path(env["SECONDBRAIN_DB_PATH"]),
            whisper_model=env.get("SECONDBRAIN_WHISPER_MODEL", "small"),
            piagent_url=env.get("SECONDBRAIN_PIAGENT_URL") or None,
            token=env.get("SECONDBRAIN_TOKEN") or None,
            tz=env.get("SECONDBRAIN_TZ", "Europe/Rome"),
        )
```

- [ ] **Step 5: `conftest.py`** (fixture riusate)

```python
import pytest
from pathlib import Path
from secondbrain.config import Config

@pytest.fixture
def vault(tmp_path: Path) -> Path:
    d = tmp_path / "vault"
    (d / "Captures").mkdir(parents=True)
    (d / "Daily").mkdir()
    (d / "_audio").mkdir()
    return d

@pytest.fixture
def config(vault: Path, tmp_path: Path) -> Config:
    return Config(vault_dir=vault, db_path=tmp_path / "index.db")
```

- [ ] **Step 6: Verifica pass**

Run: `cd backend && python -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/requirements.txt backend/secondbrain/__init__.py backend/secondbrain/config.py backend/tests/conftest.py backend/tests/test_config.py
git commit -m "backend: scaffold, dipendenze e config da env"
```

---

### Task 2: Vault writer (Markdown + frontmatter)

**Files:**
- Create: `backend/secondbrain/models.py`
- Create: `backend/secondbrain/vault.py`
- Test: `backend/tests/test_vault.py`

**Interfaces:**
- Consumes: `Config` (Task 1).
- Produces:
  - `models.Capture(id: str, created_at: datetime, transcript: str, tags: list[str]=["inbox"], audio_ref: str|None=None, source: str="device", type: str="capture")`
  - `vault.slugify(text: str, max_words: int=4) -> str`
  - `vault.capture_relpath(cap: Capture) -> str`  → es. `Captures/2026-09-14-1523-testo.md`
  - `vault.render_markdown(cap: Capture) -> str`
  - `vault.write_capture(vault_dir: Path, cap: Capture) -> str`  → ritorna il path **relativo** scritto

- [ ] **Step 1: Test che fallisce** (`tests/test_vault.py`)

```python
from datetime import datetime, timezone, timedelta
from secondbrain.models import Capture
from secondbrain import vault as vaultmod   # alias: la fixture pytest si chiama `vault`

ROME = timezone(timedelta(hours=2))

def _cap():
    return Capture(
        id="cap_20260914_1523",
        created_at=datetime(2026, 9, 14, 15, 23, tzinfo=ROME),
        transcript="Comprare il caffè e chiamare Mario",
    )

def test_slugify():
    assert vaultmod.slugify("Comprare il caffè e chiamare Mario") == "comprare-il-caffe-e"
    assert vaultmod.slugify("") == "nota"

def test_capture_relpath():
    assert vaultmod.capture_relpath(_cap()) == "Captures/2026-09-14-1523-comprare-il-caffe-e.md"

def test_render_markdown_has_frontmatter_and_body():
    md = vaultmod.render_markdown(_cap())
    assert md.startswith("---\n")
    assert "id: cap_20260914_1523" in md
    assert "type: capture" in md
    assert "created: '2026-09-14T15:23:00+02:00'" in md or "created: 2026-09-14T15:23:00+02:00" in md
    assert "Comprare il caffè e chiamare Mario" in md

def test_write_capture_creates_file(vault):   # `vault` = fixture Path (conftest)
    rel = vaultmod.write_capture(vault, _cap())
    p = vault / rel
    assert p.exists()
    assert p.read_text(encoding="utf-8").count("---") >= 2
```

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_vault.py -v`
Expected: FAIL (`ModuleNotFoundError` / attributi mancanti)

- [ ] **Step 3: Implementa `models.py`**

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Capture:
    id: str
    created_at: datetime
    transcript: str
    tags: list[str] = field(default_factory=lambda: ["inbox"])
    audio_ref: str | None = None
    source: str = "device"
    type: str = "capture"

@dataclass
class IndexedNote:
    id: str
    path: str
    type: str
    created_at: str
    tags: list[str]
    audio_ref: str | None
    enriched: bool
    mtime: str
```

- [ ] **Step 4: Implementa `vault.py`**

```python
import os
import re
import unicodedata
from pathlib import Path
import frontmatter
from secondbrain.models import Capture

def slugify(text: str, max_words: int = 4) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-zA-Z0-9]+", norm.lower())
    slug = "-".join(words[:max_words])
    return slug or "nota"

def capture_relpath(cap: Capture) -> str:
    stamp = cap.created_at.strftime("%Y-%m-%d-%H%M")
    return f"Captures/{stamp}-{slugify(cap.transcript)}.md"

def render_markdown(cap: Capture) -> str:
    post = frontmatter.Post(
        cap.transcript,
        id=cap.id,
        type=cap.type,
        created=cap.created_at.isoformat(),
        tags=cap.tags,
        source=cap.source,
        audio=cap.audio_ref,
    )
    return frontmatter.dumps(post) + "\n"

def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)

def write_capture(vault_dir: Path, cap: Capture) -> str:
    rel = capture_relpath(cap)
    _atomic_write(vault_dir / rel, render_markdown(cap))
    return rel
```

- [ ] **Step 5: Verifica pass**

Run: `cd backend && python -m pytest tests/test_vault.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/models.py backend/secondbrain/vault.py backend/tests/test_vault.py
git commit -m "backend: vault writer markdown+frontmatter con scrittura atomica"
```

---

### Task 3: Indice SQLite/FTS5

**Files:**
- Create: `backend/secondbrain/index.py`
- Test: `backend/tests/test_index.py`

**Interfaces:**
- Consumes: `IndexedNote` (Task 2), `Config`.
- Produces:
  - `index.connect(db_path: Path) -> sqlite3.Connection`
  - `index.init_db(conn) -> None`
  - `index.upsert_note(conn, note: IndexedNote, body: str) -> None`
  - `index.search(conn, query: str, limit: int=20) -> list[IndexedNote]`
  - `index.has_capture(conn, capture_id: str) -> bool`
  - `index.mark_received(conn, capture_id: str) -> None`
  - `index.reindex(conn, vault_dir: Path) -> int`

- [ ] **Step 1: Test che fallisce** (`tests/test_index.py`)

```python
from secondbrain import index
from secondbrain.models import IndexedNote

def _note(id="cap_1", body="comprare il caffe"):
    return IndexedNote(id=id, path=f"Captures/{id}.md", type="capture",
                       created_at="2026-09-14T15:23:00+02:00", tags=["inbox"],
                       audio_ref=None, enriched=False, mtime="0")

def test_upsert_and_search(tmp_path):
    conn = index.connect(tmp_path / "i.db"); index.init_db(conn)
    index.upsert_note(conn, _note(), "comprare il caffe e chiamare mario")
    hits = index.search(conn, "caffe")
    assert [h.id for h in hits] == ["cap_1"]
    assert index.search(conn, "inesistente") == []

def test_upsert_is_idempotent_on_id(tmp_path):
    conn = index.connect(tmp_path / "i.db"); index.init_db(conn)
    index.upsert_note(conn, _note(), "primo testo")
    index.upsert_note(conn, _note(body="secondo"), "secondo testo")
    assert len(index.search(conn, "secondo")) == 1
    assert index.search(conn, "primo") == []

def test_capture_idempotency(tmp_path):
    conn = index.connect(tmp_path / "i.db"); index.init_db(conn)
    assert index.has_capture(conn, "cap_1") is False
    index.mark_received(conn, "cap_1")
    assert index.has_capture(conn, "cap_1") is True

def test_reindex_from_vault(tmp_path, vault):
    (vault / "Captures" / "2026-09-14-1523-x.md").write_text(
        "---\nid: cap_x\ntype: capture\ncreated: 2026-09-14T15:23:00+02:00\ntags: [inbox]\n---\ncorpo cercabile",
        encoding="utf-8")
    conn = index.connect(tmp_path / "i.db"); index.init_db(conn)
    n = index.reindex(conn, vault)
    assert n == 1
    assert [h.id for h in index.search(conn, "cercabile")] == ["cap_x"]
```

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_index.py -v`
Expected: FAIL

- [ ] **Step 3: Implementa `index.py`**

```python
import json
import sqlite3
from pathlib import Path
import frontmatter
from secondbrain.models import IndexedNote

def connect(db_path: Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS notes(
          id TEXT PRIMARY KEY, path TEXT, type TEXT, created_at TEXT,
          tags TEXT, audio_ref TEXT, enriched INTEGER DEFAULT 0, mtime TEXT
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
          id UNINDEXED, body, tokenize='unicode61'
        );
        CREATE TABLE IF NOT EXISTS sync_state(id TEXT PRIMARY KEY, received_at TEXT);
        """
    )
    conn.commit()

def _row_to_note(r: sqlite3.Row) -> IndexedNote:
    return IndexedNote(id=r["id"], path=r["path"], type=r["type"],
                       created_at=r["created_at"], tags=json.loads(r["tags"] or "[]"),
                       audio_ref=r["audio_ref"], enriched=bool(r["enriched"]), mtime=r["mtime"])

def upsert_note(conn: sqlite3.Connection, note: IndexedNote, body: str) -> None:
    conn.execute(
        """INSERT INTO notes(id,path,type,created_at,tags,audio_ref,enriched,mtime)
           VALUES(?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET path=excluded.path,type=excluded.type,
             created_at=excluded.created_at,tags=excluded.tags,
             audio_ref=excluded.audio_ref,enriched=excluded.enriched,mtime=excluded.mtime""",
        (note.id, note.path, note.type, note.created_at, json.dumps(note.tags),
         note.audio_ref, int(note.enriched), note.mtime),
    )
    conn.execute("DELETE FROM notes_fts WHERE id=?", (note.id,))
    conn.execute("INSERT INTO notes_fts(id, body) VALUES(?,?)", (note.id, body))
    conn.commit()

def search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[IndexedNote]:
    rows = conn.execute(
        """SELECT n.* FROM notes_fts f JOIN notes n ON n.id=f.id
           WHERE notes_fts MATCH ? ORDER BY n.created_at DESC LIMIT ?""",
        (query, limit),
    ).fetchall()
    return [_row_to_note(r) for r in rows]

def has_capture(conn: sqlite3.Connection, capture_id: str) -> bool:
    return conn.execute("SELECT 1 FROM sync_state WHERE id=?", (capture_id,)).fetchone() is not None

def mark_received(conn: sqlite3.Connection, capture_id: str) -> None:
    conn.execute("INSERT OR IGNORE INTO sync_state(id, received_at) VALUES(?, datetime('now'))",
                 (capture_id,))
    conn.commit()

def reindex(conn: sqlite3.Connection, vault_dir: Path) -> int:
    conn.execute("DELETE FROM notes"); conn.execute("DELETE FROM notes_fts"); conn.commit()
    count = 0
    for md in Path(vault_dir).rglob("*.md"):
        post = frontmatter.load(md)
        note = IndexedNote(
            id=post.get("id") or str(md.relative_to(vault_dir)),
            path=str(md.relative_to(vault_dir)), type=post.get("type", "capture"),
            created_at=str(post.get("created", "")), tags=list(post.get("tags", []) or []),
            audio_ref=post.get("audio"), enriched=bool(post.get("enriched", False)),
            mtime=str(md.stat().st_mtime),
        )
        upsert_note(conn, note, post.content)
        count += 1
    return count
```

- [ ] **Step 4: Verifica pass**

Run: `cd backend && python -m pytest tests/test_index.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/index.py backend/tests/test_index.py
git commit -m "backend: indice SQLite/FTS5 con upsert, ricerca, idempotenza e reindex"
```

---

### Task 4: Astrazione STT (Transcriber)

**Files:**
- Create: `backend/secondbrain/stt.py`
- Test: `backend/tests/test_stt.py`

**Interfaces:**
- Consumes: `Config`.
- Produces:
  - `stt.Transcriber` (Protocol) con `transcribe(self, wav_bytes: bytes) -> str`
  - `stt.FakeTranscriber(text: str)` (per i test)
  - `stt.WhisperTranscriber(model_name: str, language: str="it")` (lazy-load faster-whisper)

- [ ] **Step 1: Test che fallisce** (`tests/test_stt.py`)

```python
from secondbrain.stt import FakeTranscriber, Transcriber

def test_fake_transcriber_returns_text():
    t: Transcriber = FakeTranscriber("ciao mondo")
    assert t.transcribe(b"RIFF....") == "ciao mondo"
    assert t.calls == 1
```

> `WhisperTranscriber` NON è unit-testato (richiede il modello). Verrà provato manualmente in Fase 1 con un WAV reale; qui garantiamo solo l'interfaccia.

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_stt.py -v`
Expected: FAIL

- [ ] **Step 3: Implementa `stt.py`**

```python
from typing import Protocol

class Transcriber(Protocol):
    def transcribe(self, wav_bytes: bytes) -> str: ...

class FakeTranscriber:
    def __init__(self, text: str):
        self.text = text
        self.calls = 0
    def transcribe(self, wav_bytes: bytes) -> str:
        self.calls += 1
        return self.text

class WhisperTranscriber:
    def __init__(self, model_name: str, language: str = "it"):
        self.model_name = model_name
        self.language = language
        self._model = None
    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
        return self._model
    def transcribe(self, wav_bytes: bytes) -> str:
        import io
        segments, _ = self._load().transcribe(io.BytesIO(wav_bytes), language=self.language)
        return " ".join(s.text.strip() for s in segments).strip()
```

- [ ] **Step 4: Verifica pass**

Run: `cd backend && python -m pytest tests/test_stt.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/stt.py backend/tests/test_stt.py
git commit -m "backend: astrazione STT con Transcriber, fake e wrapper faster-whisper"
```

---

### Task 5: API `POST /captures` (flusso end-to-end Fase 1)

**Files:**
- Create: `backend/secondbrain/app.py`
- Test: `backend/tests/test_captures_api.py`

**Interfaces:**
- Consumes: `Config`, `vault.write_capture`, `index.*`, `stt.Transcriber`, `enrich.Enricher` (Task 8; qui si usa `NullEnricher`, definito ora minimale in `enrich.py`).
- Produces:
  - `enrich.Enricher` (Protocol) con `enrich(self, transcript: str) -> Enrichment | None` e `enrich.NullEnricher` che ritorna `None`
  - `app.create_app(config: Config, transcriber: Transcriber, enricher: Enricher) -> FastAPI`
  - Endpoint `POST /captures` (multipart): file `audio`, form `id`, `created_at` (ISO8601), `battery` (int, opz.). Risposta JSON `{"status": "created"|"duplicate", "id": str, "path": str}`.

- [ ] **Step 1: `enrich.py` minimale** (solo Protocol + NullEnricher; PiAgent in Task 8)

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass
class Enrichment:
    tags: list[str]
    action_items: list[str]
    summary: str | None

class Enricher(Protocol):
    def enrich(self, transcript: str) -> Enrichment | None: ...

class NullEnricher:
    def enrich(self, transcript: str) -> Enrichment | None:
        return None
```

- [ ] **Step 2: Test che fallisce** (`tests/test_captures_api.py`)

```python
from fastapi.testclient import TestClient
from secondbrain.app import create_app
from secondbrain.stt import FakeTranscriber
from secondbrain.enrich import NullEnricher

def _client(config):
    return TestClient(create_app(config, FakeTranscriber("comprare il caffe"), NullEnricher()))

def _post(client, id="cap_1"):
    return client.post("/captures",
        files={"audio": ("a.wav", b"RIFFxxxx", "audio/wav")},
        data={"id": id, "created_at": "2026-09-14T15:23:00+02:00", "battery": "82"})

def test_capture_creates_note_and_indexes(config, vault):
    client = _client(config)
    r = _post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "created"
    assert body["path"].startswith("Captures/")
    assert (vault / body["path"]).exists()
    hits = client.get("/notes", params={"q": "caffe"}).json()
    assert hits[0]["id"] == "cap_1"

def test_capture_is_idempotent(config):
    client = _client(config)
    first = _post(client).json()
    second = _post(client).json()
    assert second["status"] == "duplicate"
    assert second["path"] == first["path"]
```

> Nota: questo test usa anche `GET /notes`, implementato in Task 6. Se esegui i task in ordine, sposta l'asserzione su `/notes` a Task 6; altrimenti implementa Task 6 subito dopo lo Step 4 e poi rilancia.

- [ ] **Step 3: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_captures_api.py::test_capture_is_idempotent -v`
Expected: FAIL

- [ ] **Step 4: Implementa `app.py`** (con `/captures`; `/notes` arriva in Task 6)

```python
from datetime import datetime
from fastapi import FastAPI, UploadFile, File, Form
from secondbrain.config import Config
from secondbrain.stt import Transcriber
from secondbrain.enrich import Enricher
from secondbrain.models import Capture, IndexedNote
from secondbrain import vault, index

def create_app(config: Config, transcriber: Transcriber, enricher: Enricher) -> FastAPI:
    app = FastAPI(title="secondbrain")
    conn = index.connect(config.db_path); index.init_db(conn)
    app.state.config = config
    app.state.conn = conn

    @app.post("/captures")
    async def create_capture(
        audio: UploadFile = File(...),
        id: str = Form(...),
        created_at: str = Form(...),
        battery: int | None = Form(None),
    ):
        if index.has_capture(conn, id):
            existing = conn.execute("SELECT path FROM notes WHERE id=?", (id,)).fetchone()
            return {"status": "duplicate", "id": id, "path": existing["path"] if existing else ""}

        wav = await audio.read()
        text = transcriber.transcribe(wav)
        cap = Capture(id=id, created_at=datetime.fromisoformat(created_at), transcript=text)

        enr = enricher.enrich(text)
        if enr:
            cap.tags = sorted(set(cap.tags) | set(enr.tags))

        rel = vault.write_capture(config.vault_dir, cap)
        note = IndexedNote(id=id, path=rel, type="capture", created_at=cap.created_at.isoformat(),
                           tags=cap.tags, audio_ref=cap.audio_ref, enriched=bool(enr), mtime="0")
        index.upsert_note(conn, note, text)
        index.mark_received(conn, id)
        return {"status": "created", "id": id, "path": rel}

    return app
```

- [ ] **Step 5: Verifica pass** (idempotenza)

Run: `cd backend && python -m pytest tests/test_captures_api.py::test_capture_is_idempotent -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/app.py backend/secondbrain/enrich.py backend/tests/test_captures_api.py
git commit -m "backend: endpoint POST /captures (STT -> vault -> indice) idempotente"
```

---

### Task 6: API `GET /notes` (ricerca)

**Files:**
- Modify: `backend/secondbrain/app.py`
- Test: `backend/tests/test_notes_api.py`

**Interfaces:**
- Consumes: `index.search`.
- Produces: endpoint `GET /notes?q=<str>&limit=<int>` → JSON list di `{id, path, type, created_at, tags}`.

- [ ] **Step 1: Test che fallisce** (`tests/test_notes_api.py`)

```python
from fastapi.testclient import TestClient
from secondbrain.app import create_app
from secondbrain.stt import FakeTranscriber
from secondbrain.enrich import NullEnricher

def test_notes_search_returns_matches(config):
    client = TestClient(create_app(config, FakeTranscriber("riunione con Mario"), NullEnricher()))
    client.post("/captures", files={"audio": ("a.wav", b"RIFF", "audio/wav")},
                data={"id": "cap_9", "created_at": "2026-09-14T09:00:00+02:00"})
    r = client.get("/notes", params={"q": "riunione"})
    assert r.status_code == 200
    assert r.json()[0]["id"] == "cap_9"
    assert client.get("/notes", params={"q": "xyz"}).json() == []
```

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_notes_api.py -v`
Expected: FAIL (404 su `/notes`)

- [ ] **Step 3: Aggiungi l'endpoint in `app.py`** (dentro `create_app`, prima di `return app`)

```python
    @app.get("/notes")
    def list_notes(q: str, limit: int = 20):
        return [
            {"id": n.id, "path": n.path, "type": n.type,
             "created_at": n.created_at, "tags": n.tags}
            for n in index.search(conn, q, limit)
        ]
```

- [ ] **Step 4: Verifica pass** (anche il test rimandato di Task 5)

Run: `cd backend && python -m pytest tests/test_notes_api.py tests/test_captures_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/app.py backend/tests/test_notes_api.py
git commit -m "backend: endpoint GET /notes con ricerca full-text"
```

---

### Task 7: Digest renderer + `GET /digest.bmp`

**Files:**
- Create: `backend/secondbrain/digest.py`
- Modify: `backend/secondbrain/app.py`
- Test: `backend/tests/test_digest.py`

**Interfaces:**
- Consumes: `Config`.
- Produces:
  - `models.DigestData(now: datetime, next_event: str|None, tasks: list[str], last_note: str|None, journal_prompt: str|None, temp_c: float|None, humidity: float|None, battery: int|None)`
  - `digest.render_digest(data: DigestData) -> bytes` (BMP 200×200, modo `"1"`)
  - endpoint `GET /digest.bmp?battery=<int>&temp=<float>&humidity=<float>` → `image/bmp`

- [ ] **Step 1: Aggiungi `DigestData` a `models.py`**

```python
@dataclass
class DigestData:
    now: datetime
    next_event: str | None = None
    tasks: list[str] = field(default_factory=list)
    last_note: str | None = None
    journal_prompt: str | None = None
    temp_c: float | None = None
    humidity: float | None = None
    battery: int | None = None
```

- [ ] **Step 2: Test che fallisce** (`tests/test_digest.py`)

```python
import io
from datetime import datetime
from PIL import Image
from secondbrain.digest import render_digest
from secondbrain.models import DigestData

def test_render_digest_is_200x200_1bit_bmp():
    data = DigestData(now=datetime(2026, 9, 14, 8, 0), tasks=["comprare caffe"],
                      last_note="chiamare Mario", battery=82, temp_c=22.5, humidity=48)
    raw = render_digest(data)
    assert raw[:2] == b"BM"  # BMP magic
    img = Image.open(io.BytesIO(raw))
    assert img.size == (200, 200)
    assert img.mode == "1"

def test_render_digest_is_deterministic():
    data = DigestData(now=datetime(2026, 9, 14, 8, 0), battery=50)
    assert render_digest(data) == render_digest(data)
```

- [ ] **Step 3: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_digest.py -v`
Expected: FAIL

- [ ] **Step 4: Implementa `digest.py`**

```python
import io
from PIL import Image, ImageDraw
from secondbrain.models import DigestData

W = H = 200

def render_digest(data: DigestData) -> bytes:
    img = Image.new("1", (W, H), 1)  # 1 = bianco
    d = ImageDraw.Draw(img)
    y = 4
    def line(text: str):
        nonlocal y
        d.text((4, y), text[:34], fill=0)
        y += 12
    line(data.now.strftime("%a %d %b  %H:%M"))
    d.line([(4, y), (W - 4, y)], fill=0); y += 4
    if data.next_event: line(f"> {data.next_event}")
    for t in data.tasks[:5]: line(f"[ ] {t}")
    if data.last_note: line(f"~ {data.last_note}")
    if data.journal_prompt: line(f"? {data.journal_prompt}")
    foot = []
    if data.temp_c is not None: foot.append(f"{data.temp_c:.0f}C")
    if data.humidity is not None: foot.append(f"{data.humidity:.0f}%")
    if data.battery is not None: foot.append(f"bat {data.battery}%")
    if foot:
        d.text((4, H - 14), "  ".join(foot), fill=0)
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()
```

- [ ] **Step 5: Aggiungi l'endpoint in `app.py`**

```python
    from fastapi import Response
    from secondbrain.models import DigestData
    from secondbrain import digest as digest_mod
    from datetime import datetime as _dt

    @app.get("/digest.bmp")
    def get_digest(battery: int | None = None, temp: float | None = None, humidity: float | None = None):
        last = conn.execute("SELECT id FROM notes ORDER BY created_at DESC LIMIT 1").fetchone()
        data = DigestData(now=_dt.now(), last_note=(last["id"] if last else None),
                          battery=battery, temp_c=temp, humidity=humidity)
        return Response(content=digest_mod.render_digest(data), media_type="image/bmp")
```

- [ ] **Step 6: Verifica pass**

Run: `cd backend && python -m pytest tests/test_digest.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add backend/secondbrain/digest.py backend/secondbrain/models.py backend/secondbrain/app.py backend/tests/test_digest.py
git commit -m "backend: render digest 200x200 1-bit BMP e endpoint GET /digest.bmp"
```

---

### Task 8: Enricher PiAgent (on-demand, con fallback)

**Files:**
- Modify: `backend/secondbrain/enrich.py`
- Test: `backend/tests/test_enrich.py`

**Interfaces:**
- Consumes: `Config.piagent_url`, `Enrichment`/`Enricher` (Task 5).
- Produces: `enrich.PiAgentEnricher(base_url: str, token: str|None=None, timeout: float=5.0, client: httpx.Client|None=None)`; ritorna `Enrichment` in caso di successo, `None` su errore/timeout (fallback: la nota resta salvata). Contratto: `POST {base_url}/enrich` con JSON `{"text": <transcript>}`, risposta `{"tags": [...], "action_items": [...], "summary": "..."}`.

- [ ] **Step 1: Test che fallisce** (`tests/test_enrich.py`)

```python
import httpx
from secondbrain.enrich import PiAgentEnricher

def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))

def test_enrich_success_parses_response():
    def handler(req):
        assert req.url.path == "/enrich"
        return httpx.Response(200, json={"tags": ["spesa"], "action_items": ["comprare caffe"], "summary": "s"})
    e = PiAgentEnricher("http://piagent", client=_client(handler))
    out = e.enrich("comprare il caffe")
    assert out.tags == ["spesa"]
    assert out.action_items == ["comprare caffe"]
    assert out.summary == "s"

def test_enrich_returns_none_on_error():
    def handler(req):
        return httpx.Response(500)
    e = PiAgentEnricher("http://piagent", client=_client(handler))
    assert e.enrich("x") is None

def test_enrich_returns_none_on_timeout():
    def handler(req):
        raise httpx.TimeoutException("slow")
    e = PiAgentEnricher("http://piagent", client=_client(handler))
    assert e.enrich("x") is None
```

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_enrich.py -v`
Expected: FAIL (`PiAgentEnricher` inesistente)

- [ ] **Step 3: Estendi `enrich.py`** (aggiungi in coda)

```python
import httpx

class PiAgentEnricher:
    def __init__(self, base_url: str, token: str | None = None,
                 timeout: float = 5.0, client: httpx.Client | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = client or httpx.Client(timeout=timeout, headers=headers)

    def enrich(self, transcript: str) -> Enrichment | None:
        try:
            r = self._client.post(f"{self.base_url}/enrich", json={"text": transcript})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError):
            return None
        return Enrichment(
            tags=list(data.get("tags", [])),
            action_items=list(data.get("action_items", [])),
            summary=data.get("summary"),
        )
```

- [ ] **Step 4: Verifica pass**

Run: `cd backend && python -m pytest tests/test_enrich.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/enrich.py backend/tests/test_enrich.py
git commit -m "backend: PiAgentEnricher on-demand con fallback su errore/timeout"
```

---

### Task 9: Reindex CLI + file watcher

**Files:**
- Create: `backend/secondbrain/watcher.py`
- Test: `backend/tests/test_watcher.py`

**Interfaces:**
- Consumes: `index.reindex`, `Config`.
- Produces:
  - `watcher.run_reindex(config: Config) -> int` (apre conn, chiama `index.reindex`, ritorna n. note)
  - `watcher.main(argv: list[str] | None=None) -> int` (CLI: `python -m secondbrain.watcher reindex`)

> Il file-watcher "live" (inotify) è integrazione I/O: qui testiamo solo `run_reindex` in modo deterministico; il loop di watch è un wrapper sottile provato manualmente.

- [ ] **Step 1: Test che fallisce** (`tests/test_watcher.py`)

```python
from secondbrain.watcher import run_reindex

def test_run_reindex_counts_vault_files(config, vault):
    (vault / "Captures" / "n1.md").write_text(
        "---\nid: n1\ntype: capture\ncreated: 2026-09-14T10:00:00+02:00\ntags: [inbox]\n---\nprimo",
        encoding="utf-8")
    (vault / "Daily" / "2026-09-14.md").write_text(
        "---\nid: d1\ntype: journal\ncreated: 2026-09-14T00:00:00+02:00\ntags: []\n---\ndiario",
        encoding="utf-8")
    assert run_reindex(config) == 2
```

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_watcher.py -v`
Expected: FAIL

- [ ] **Step 3: Implementa `watcher.py`**

```python
import os
import sys
from secondbrain.config import Config
from secondbrain import index

def run_reindex(config: Config) -> int:
    conn = index.connect(config.db_path)
    index.init_db(conn)
    return index.reindex(conn, config.vault_dir)

def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    config = Config.from_env(os.environ)
    if argv and argv[0] == "reindex":
        print(f"reindexed {run_reindex(config)} notes")
        return 0
    print("usage: python -m secondbrain.watcher reindex")
    return 1

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Verifica pass**

Run: `cd backend && python -m pytest tests/test_watcher.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/watcher.py backend/tests/test_watcher.py
git commit -m "backend: reindex CLI del vault"
```

---

### Task 10: Entrypoint, Docker e compose

**Files:**
- Create: `backend/secondbrain/main.py`
- Create: `backend/Dockerfile`
- Create: `backend/docker-compose.yml`
- Create: `backend/.dockerignore`
- Test: `backend/tests/test_main.py`

**Interfaces:**
- Consumes: `Config.from_env`, `create_app`, `WhisperTranscriber`, `PiAgentEnricher`/`NullEnricher`.
- Produces: `main.build_app() -> FastAPI` (costruisce da `os.environ`); modulo eseguibile da uvicorn come `secondbrain.main:app`.

- [ ] **Step 1: Test che fallisce** (`tests/test_main.py`)

```python
import os
from fastapi.testclient import TestClient

def test_build_app_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SECONDBRAIN_VAULT_DIR", str(tmp_path / "vault"))
    monkeypatch.setenv("SECONDBRAIN_DB_PATH", str(tmp_path / "i.db"))
    monkeypatch.delenv("SECONDBRAIN_PIAGENT_URL", raising=False)
    from secondbrain.main import build_app
    client = TestClient(build_app())
    assert client.get("/notes", params={"q": "x"}).json() == []
```

- [ ] **Step 2: Verifica fallimento**

Run: `cd backend && python -m pytest tests/test_main.py -v`
Expected: FAIL

- [ ] **Step 3: Implementa `main.py`**

```python
import os
from fastapi import FastAPI
from secondbrain.config import Config
from secondbrain.app import create_app
from secondbrain.stt import WhisperTranscriber
from secondbrain.enrich import PiAgentEnricher, NullEnricher

def build_app() -> FastAPI:
    config = Config.from_env(os.environ)
    transcriber = WhisperTranscriber(config.whisper_model)
    enricher = PiAgentEnricher(config.piagent_url, config.token) if config.piagent_url else NullEnricher()
    return create_app(config, transcriber, enricher)

app = build_app()
```

- [ ] **Step 4: Verifica pass**

Run: `cd backend && python -m pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: `Dockerfile`**

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY secondbrain ./secondbrain
EXPOSE 8000
CMD ["uvicorn", "secondbrain.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: `.dockerignore`**

```
tests/
__pycache__/
*.pyc
.pytest_cache/
```

- [ ] **Step 7: `docker-compose.yml`**

```yaml
services:
  secondbrain:
    build: .
    container_name: secondbrain
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      SECONDBRAIN_VAULT_DIR: /data/vault
      SECONDBRAIN_DB_PATH: /data/index.db
      SECONDBRAIN_WHISPER_MODEL: small
      SECONDBRAIN_TZ: Europe/Rome
      # SECONDBRAIN_PIAGENT_URL: http://piagent:8080
      # SECONDBRAIN_TOKEN: <token>
    volumes:
      - ${VAULT_HOST_PATH:-./vault}:/data/vault
      - secondbrain_state:/data

volumes:
  secondbrain_state:
```

- [ ] **Step 8: Verifica build immagine**

Run: `cd backend && docker compose build`
Expected: build completa senza errori

- [ ] **Step 9: Commit**

```bash
git add backend/secondbrain/main.py backend/Dockerfile backend/docker-compose.yml backend/.dockerignore backend/tests/test_main.py
git commit -m "backend: entrypoint uvicorn, Dockerfile e docker-compose"
```

---

## Note di integrazione (post-piano)

- **Contratto API per il firmware** (da usare quando scriveremo il piano firmware dopo la Fase 0):
  - `POST /captures` multipart → `{status, id, path}`
  - `GET /digest.bmp?battery=&temp=&humidity=` → `image/bmp` 200×200 1-bit
- **Prova manuale Fase 1** (dopo Task 5, con modello reale): registra un WAV 16 kHz mono, `curl -F audio=@nota.wav -F id=cap_test -F created_at=2026-09-14T15:23:00+02:00 http://localhost:8000/captures`, verifica il `.md` nel vault e apri il vault in Obsidian.
- **Syncthing** (Fase 4): configurato a livello CasaOS puntando alla stessa cartella `VAULT_HOST_PATH`; fuori dallo scope di questo piano di codice.

## Self-Review

- **Copertura spec:** cattura→trascrizione→vault (Task 2,4,5) ✓; indice/ricerca (Task 3,6) ✓; idempotenza catture (Task 3,5) ✓; digest 200×200 1-bit (Task 7) ✓; PiAgent on-demand con fallback (Task 8) ✓; reindex/watcher (Task 9) ✓; Docker/compose + vault come volume (Task 10) ✓; STT locale (Task 4,10) ✓; scrittura atomica (Task 2) ✓. **Fuori scope volutamente:** firmware (piano separato post-bring-up), Syncthing (config infra), embeddings (Fase 5).
- **Type consistency:** `Transcriber.transcribe(bytes)->str`, `Enricher.enrich(str)->Enrichment|None`, `IndexedNote`/`Capture`/`DigestData` usati coerentemente tra i task; `create_app(config, transcriber, enricher)` invariato in Task 5/6/7 e chiamato in Task 10.
- **Placeholder:** nessun TODO/TBD; ogni step ha codice reale. Unica dipendenza d'ordine segnalata: un'asserzione di Task 5 usa `GET /notes` (Task 6) — nota esplicita nel task.
