# Backend — Note nel vault Obsidian — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Le note `.md` delle registrazioni vivono in un vault Obsidian (`<vault>/Captures/<anno>/<data> <ora> <titolo>.md`), scritte dal backend e modificabili da Obsidian; la UI web continua a mostrare e correggere i campi AI e a elencare il file della nota.

**Architecture:** Un modulo `vault.py` incapsula cartella, nomi dei file (funzione pura `vault_note_name`), scrittura atomica, rinomina ed elenco. `notes.write_note` scrive nel vault e tiene `note_path`/`note_mtime` nel catalogo; le operazioni di `library` (titolo, data, cestino, ripristino, eliminazione) spostano o rinominano la nota. `vault_sync.check` (chiamato dal worker ogni minuto quando è libero) rilegge i file cambiati e applica le differenze come correzioni a mano. `vault migrate` sposta le note esistenti; `rescan` ricollega per `id`.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, Postgres (test su `docker-compose.test.yml`, porta 55432), PyYAML, pytest (`uv run pytest -q`).

**Spec:** `docs/specs/2026-10-07-backend-vault-obsidian-design.md` (i §x citati sono i suoi).

## Global Constraints

- Test: `cd backend && docker compose -f docker-compose.test.yml up -d && uv run pytest -q` (oggi 502 verdi). Ogni task finisce con la suite verde.
- Commit in italiano con prefisso `backend:`/`docs:`, nessun riferimento all'assistente, autore `francecesco`. Branch `backend-vault`.
- Chi scrive campi AI tiene il lock di riga (`notes.lock_capture`) e riparte dai valori riletti (spec AI §10): vale anche per `vault_sync`.
- Scritture su disco atomiche (temporaneo + `os.replace`), come in `archive.py`.
- Nessun segreto nei log; i percorsi del vault sì.
- La UI segue `docs/stile-ui.md`: le modifiche ai template si verificano con screenshot (chiaro, scuro, 390 px).
- Nomi delle colonne nuove: `note_path` (String 255, unique, nullable), `note_mtime` (Double, nullable).

## Review Focus

1. Titolo con `/`, `:` o emoji e titolo lunghissimo → nome di file valido su macOS, Linux e Android (Syncthing), troncato, senza rinomina a ogni ciclo se nulla cambia (Task 2).
2. Due registrazioni nello stesso minuto con lo stesso titolo → due file distinti, nessuna sovrascrittura (Task 2, collisione ` (2)`).
3. Modifica in Obsidian mentre il worker sta elaborando la stessa nota → il lock di riga serializza, `edited` protegge il campo corretto, nessuna scrittura persa (Task 5, test con job `running`).
4. Nota nel cestino → file assente dal vault; ripristino → file di nuovo con il nome atteso; eliminazione definitiva → nessun file orfano nel vault né nell'archivio (Task 4).
5. Vault vuoto o `VAULT_DIR` non montato → il backend parte lo stesso, la prima scrittura crea `Captures/`; se la cartella non è scrivibile l'errore è nel log e nella riga di Impostazioni, non un crash del worker (Task 1 e 5).

---

## File Structure

- `backend/secondbrain/config.py`: `vault_dir`, `public_url`.
- `backend/secondbrain/models.py`: `note_path`, `note_mtime`. `backend/migrations/versions/0005_vault_notes.py`.
- `backend/secondbrain/vault.py` (nuovo): `vault_note_name`, `clean_title_for_name`, `Vault`.
- `backend/secondbrain/notefile.py`: `NoteMeta` e `render_note(note, tz, meta)`.
- `backend/secondbrain/notes.py`: `write_note` nel vault, `expected_note_path`, `note_meta`.
- `backend/secondbrain/library.py`: titolo, data, cestino, ripristino, eliminazione, purge con la nota.
- `backend/secondbrain/vault_sync.py` (nuovo): `check`, `VaultReport`.
- `backend/secondbrain/worker.py`: chiamata periodica a `vault_sync.check`.
- `backend/secondbrain/rescan.py`, `backend/secondbrain/cli.py` (`vault migrate`), `backend/secondbrain/vault_migrate.py` (nuovo).
- `backend/secondbrain/web/browse.py` (rotta `/captures/{id}/note`), `templates/_detail.html`, `web/settings.py` + `templates/settings.html` (riga vault).
- `backend/docker-compose.yml`, `backend/.env.example`, `backend/README.md`, spec (Stato), `CLAUDE.md`.
- Test: `tests/test_vault.py`, `tests/test_vault_sync.py`, `tests/test_vault_migrate.py` (nuovi); `tests/test_config.py`, `tests/test_notefile.py`, `tests/test_notes.py`, `tests/test_library.py`, `tests/test_browse.py`, `tests/test_rescan.py`, `tests/test_worker.py`, `tests/conftest.py` (`vault_dir` nelle impostazioni di test).

---

### Task 1: Configurazione, modello e migrazione

**Files:**
- Modify: `backend/secondbrain/config.py` (`Settings`, `load_settings`), `backend/secondbrain/models.py` (`Capture`), `backend/secondbrain/app.py` (`app.state.vault`), `backend/tests/conftest.py` (fixture `settings`), `backend/docker-compose.yml`, `backend/.env.example`
- Create: `backend/migrations/versions/0005_vault_notes.py`
- Test: `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings.vault_dir: Path`, `Settings.public_url: str | None`; `Capture.note_path`, `Capture.note_mtime`; `app.state.vault` (istanza di `Vault`, Task 2: in questo task è `None` finché `vault.py` non esiste → si crea `vault.py` con la sola classe `Vault(root)` minimale qui, Task 2 la completa).

- [ ] **Step 1: Test che fallisce** (`tests/test_config.py`, in coda al file, con lo stile dei test esistenti):

```python
def test_vault_dir_and_public_url(monkeypatch):
    env = {"DATABASE_URL": "postgresql+psycopg://x", "VAULT_DIR": "/data/vault",
           "PUBLIC_URL": "https://brain.esempio.it/"}
    s = load_settings(env)
    assert s.vault_dir == Path("/data/vault")
    assert s.public_url == "https://brain.esempio.it"   # senza barra finale


def test_vault_defaults():
    s = load_settings({"DATABASE_URL": "postgresql+psycopg://x"})
    assert s.vault_dir == Path("/data/vault") and s.public_url is None
```

(Se `load_settings` legge `os.environ` senza parametro, adattare come fanno gli altri test del file.)

- [ ] **Step 2: Verificare che fallisca**: `uv run pytest -q tests/test_config.py` → `AttributeError: vault_dir`.

- [ ] **Step 3: Implementazione**

`config.py`: in `Settings` aggiungere `vault_dir: Path` dopo `firmware_dir` e `public_url: str | None = None`; in `load_settings`: `vault_dir=Path(env.get("VAULT_DIR", "/data/vault"))`, `public_url=env.get("PUBLIC_URL", "").strip().rstrip("/") or None`.

`models.py`, in `Capture` dopo `processed_at`:

```python
    # Nota nel vault Obsidian (spec vault §4): percorso relativo a VAULT_DIR e data di
    # modifica del file dopo l'ultima scrittura/lettura del backend. NULL: mai scritta o nel cestino.
    note_path: Mapped[str | None] = mapped_column(String(255), unique=True)
    note_mtime: Mapped[float | None] = mapped_column(Double)
```

`migrations/versions/0005_vault_notes.py` (copiare intestazione e `down_revision` da `0004_ai_processing.py`):

```python
def upgrade() -> None:
    op.add_column("captures", sa.Column("note_path", sa.String(255), nullable=True))
    op.add_column("captures", sa.Column("note_mtime", sa.Double(), nullable=True))
    op.create_unique_constraint("uq_captures_note_path", "captures", ["note_path"])


def downgrade() -> None:
    op.drop_constraint("uq_captures_note_path", "captures", type_="unique")
    op.drop_column("captures", "note_mtime")
    op.drop_column("captures", "note_path")
```

`vault.py` minimale:

```python
"""Vault Obsidian (spec vault §4-6): la cartella Captures/ con le note delle registrazioni."""
from pathlib import Path

CAPTURES_DIR = "Captures"


class Vault:
    def __init__(self, root: Path):
        self.root = root

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def ensure(self) -> None:
        (self.root / CAPTURES_DIR).mkdir(parents=True, exist_ok=True)
```

`app.py`: `app.state.vault = Vault(settings.vault_dir)` accanto a `app.state.archive`; nel lifespan, dove si chiama `archive.ensure()` (o equivalente), `vault.ensure()` dentro `try/except OSError` con `log.error("vault non scrivibile: %s", exc)`.

`conftest.py`: `settings` con `vault_dir=tmp_path / "vault"`. `docker-compose.yml`: `VAULT_DIR: /data/vault`, `PUBLIC_URL: ${PUBLIC_URL:-}` nell'ambiente comune; volume `${DATA_DIR:-./data}/vault:/data/vault` su `app` e `worker`. `.env.example`: le due variabili con commento.

- [ ] **Step 4: Suite verde**: `uv run pytest -q` → tutti verdi (la migrazione gira nel lifespan dei test o in `conftest`, come le precedenti).

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/config.py backend/secondbrain/models.py backend/secondbrain/app.py backend/secondbrain/vault.py backend/migrations/versions/0005_vault_notes.py backend/tests/conftest.py backend/tests/test_config.py backend/docker-compose.yml backend/.env.example
git commit -m "backend: VAULT_DIR, PUBLIC_URL e colonne della nota nel vault" -m "Base per le note nel vault Obsidian: cartella del vault come volume, link pubblico opzionale, note_path e note_mtime nel catalogo (migrazione 0005)."
```

---

### Task 2: `vault.py` — nome del file, scrittura atomica, rinomina, elenco

**Files:**
- Modify: `backend/secondbrain/vault.py`
- Test: `backend/tests/test_vault.py` (nuovo)

**Interfaces:**
- Produces: `clean_title_for_name(title: str) -> str`; `vault_note_name(recorded_local: datetime, title: str) -> str` (es. `"2026-10-07 11.58 Promemoria.md"`); `note_dir(recorded_local) -> str` (`"Captures/2026"`); `Vault.write_text(rel, text) -> float` (ritorna l'mtime dopo la scrittura); `Vault.rename(old_rel, new_rel) -> None`; `Vault.unique_rel(dir_rel, name) -> str` (aggiunge ` (2)` prima di `.md` se occupato); `Vault.delete(rel)`; `Vault.list_notes() -> list[tuple[str, float]]` (percorsi relativi sotto `Captures/` con mtime, saltando temporanei e conflitti); `Vault.mtime(rel) -> float | None`; `Vault.read_text(rel) -> str`.

- [ ] **Step 1: Test che fallisce** (`tests/test_vault.py`):

```python
from datetime import datetime
from pathlib import Path

from secondbrain.vault import Vault, clean_title_for_name, note_dir, vault_note_name

LOCAL = datetime(2026, 10, 7, 11, 58, 29)


def test_note_name_is_date_time_and_clean_title():
    assert vault_note_name(LOCAL, "Promemoria per le piante") == "2026-10-07 11.58 Promemoria per le piante.md"
    assert note_dir(LOCAL) == "Captures/2026"


def test_title_cleaning_removes_forbidden_characters_and_truncates():
    assert clean_title_for_name('Bolletta: luce/gas "casa" <ok>?') == "Bolletta luce gas casa ok"
    assert clean_title_for_name("  tanti   spazi  .") == "tanti spazi"
    assert len(clean_title_for_name("x" * 200)) == 80
    assert clean_title_for_name("") == "Nota"           # mai un nome vuoto
    assert clean_title_for_name("è \U0001F331") == "è \U0001F331"  # accenti ed emoji restano


def test_write_rename_list_and_unique(tmp_path):
    vault = Vault(tmp_path)
    rel = vault.unique_rel("Captures/2026", "2026-10-07 11.58 Piante.md")
    assert rel == "Captures/2026/2026-10-07 11.58 Piante.md"
    mtime = vault.write_text(rel, "---\nid: x\n---\n")
    assert (tmp_path / rel).read_text() == "---\nid: x\n---\n" and mtime == vault.mtime(rel)
    assert vault.unique_rel("Captures/2026", "2026-10-07 11.58 Piante.md") == "Captures/2026/2026-10-07 11.58 Piante (2).md"
    vault.rename(rel, "Captures/2026/2026-10-07 11.58 Fiori.md")
    assert not (tmp_path / rel).exists() and vault.mtime("Captures/2026/2026-10-07 11.58 Fiori.md")
    (tmp_path / "Captures/2026/.syncthing.x.md.tmp").write_text("x")
    (tmp_path / "Captures/2026/2026-10-07 11.58 Fiori.sync-conflict-20261007-120000-ABCDEF.md").write_text("x")
    (tmp_path / "Captures/Note mia.md").write_text("x")
    assert [rel for rel, _ in vault.list_notes()] == ["Captures/2026/2026-10-07 11.58 Fiori.md",
                                                        "Captures/Note mia.md"]
    assert vault.conflicts() == ["Captures/2026/2026-10-07 11.58 Fiori.sync-conflict-20261007-120000-ABCDEF.md"]
    vault.delete("Captures/2026/2026-10-07 11.58 Fiori.md")
    assert vault.mtime("Captures/2026/2026-10-07 11.58 Fiori.md") is None
```

- [ ] **Step 2: Verificare che fallisca**: `uv run pytest -q tests/test_vault.py` → `ImportError`.

- [ ] **Step 3: Implementazione** (`vault.py`):

```python
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

CAPTURES_DIR = "Captures"
NOTE_SUFFIX = ".md"
MAX_NAME_TITLE_LEN = 80
FALLBACK_TITLE = "Nota"
FORBIDDEN = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
SPACES = re.compile(r"\s+")
CONFLICT_MARK = ".sync-conflict-"
SKIP_PREFIXES = (".syncthing.", ".")
SKIP_SUFFIXES = (".tmp", "~")


def clean_title_for_name(title: str) -> str:
    text = SPACES.sub(" ", FORBIDDEN.sub(" ", title or "")).strip().rstrip(".").strip()
    return text[:MAX_NAME_TITLE_LEN].rstrip() or FALLBACK_TITLE


def note_dir(recorded_local: datetime) -> str:
    return f"{CAPTURES_DIR}/{recorded_local:%Y}"


def vault_note_name(recorded_local: datetime, title: str) -> str:
    return f"{recorded_local:%Y-%m-%d %H.%M} {clean_title_for_name(title)}{NOTE_SUFFIX}"


class Vault:
    def __init__(self, root: Path):
        self.root = root

    def abs(self, rel: str) -> Path:
        return self.root / rel

    def ensure(self) -> None:
        (self.root / CAPTURES_DIR).mkdir(parents=True, exist_ok=True)

    def mtime(self, rel: str) -> float | None:
        try:
            return self.abs(rel).stat().st_mtime
        except FileNotFoundError:
            return None

    def read_text(self, rel: str) -> str:
        return self.abs(rel).read_text(encoding="utf-8")

    def unique_rel(self, dir_rel: str, name: str) -> str:
        stem, k = name.removesuffix(NOTE_SUFFIX), 1
        candidate = name
        while self.abs(f"{dir_rel}/{candidate}").exists():
            k += 1
            candidate = f"{stem} ({k}){NOTE_SUFFIX}"
        return f"{dir_rel}/{candidate}"

    def write_text(self, rel: str, text: str) -> float:
        target = self.abs(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".secondbrain-", suffix=NOTE_SUFFIX, dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(text.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return target.stat().st_mtime

    def rename(self, old_rel: str, new_rel: str) -> None:
        target = self.abs(new_rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(self.abs(old_rel), target)

    def delete(self, rel: str) -> None:
        self.abs(rel).unlink(missing_ok=True)

    def _is_note(self, path: Path) -> bool:
        name = path.name
        return (path.is_file() and name.endswith(NOTE_SUFFIX) and CONFLICT_MARK not in name
                and not name.startswith(SKIP_PREFIXES) and not name.endswith(SKIP_SUFFIXES))

    def list_notes(self) -> list[tuple[str, float]]:
        base = self.root / CAPTURES_DIR
        if not base.is_dir():
            return []
        found = [(p.relative_to(self.root).as_posix(), p.stat().st_mtime)
                 for p in base.rglob("*" + NOTE_SUFFIX) if self._is_note(p)]
        return sorted(found)

    def conflicts(self) -> list[str]:
        base = self.root / CAPTURES_DIR
        if not base.is_dir():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*" + NOTE_SUFFIX)
                      if p.is_file() and CONFLICT_MARK in p.name)
```

Nota: il temporaneo inizia con `.` così Syncthing e `list_notes` lo ignorano.

- [ ] **Step 4: Test verdi**: `uv run pytest -q tests/test_vault.py`, poi la suite.

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/vault.py backend/tests/test_vault.py
git commit -m "backend: modulo vault (nomi delle note, scrittura atomica, rinomina, elenco)" -m "Nome <data> <ora> <titolo>.md con titolo ripulito e troncato, suffisso (2) sulle collisioni, temporanei e file di conflitto di Syncthing ignorati."
```

---

### Task 3: Frontmatter della nota nel vault (`NoteMeta`)

**Files:**
- Modify: `backend/secondbrain/notefile.py` (`render_note`), `backend/secondbrain/notes.py` (`note_meta`)
- Test: `backend/tests/test_notefile.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) NoteMeta(capture_id: str, device_id: str, created: datetime, duration_s: float, title: str, url: str | None)`; `render_note(note, tz, meta: NoteMeta | None = None)`; `notes.note_meta(capture, tz, public_url) -> NoteMeta` (titolo = `display_title`, spostato da `web/templating.py` in `notes.py` come `effective_title(capture, tz)` e reimportato dal template per non duplicarlo).

- [ ] **Step 1: Test che fallisce** (`tests/test_notefile.py`, in coda):

```python
def test_render_with_meta_puts_identity_first_and_aliases():
    from secondbrain.notefile import NoteMeta
    meta = NoteMeta(capture_id="cap_20261007_095829", device_id="70041dd8263c",
                    created=datetime(2026, 10, 7, 9, 58, 29, tzinfo=UTC), duration_s=6.4,
                    title="Promemoria piante", url="https://brain.esempio.it/captures/abc")
    note = Note(transcript="Testo.", title="Promemoria piante", summary="R.", tags=("piante",))
    text = render_note(note, ZoneInfo("Europe/Rome"), meta)
    head = text.split("---")[1]
    assert head.startswith("\nid: cap_20261007_095829\ndevice: 70041dd8263c\ncreated: '2026-10-07T11:58:29+02:00'\nduration: 6\n")
    assert "title: Promemoria piante\naliases: [Promemoria piante]\n" in head
    assert "url: https://brain.esempio.it/captures/abc\n" in head
    assert parse_note(text).title == "Promemoria piante"   # i campi in piu' non disturbano il parser


def test_render_with_meta_without_url_and_title_uses_meta_title():
    from secondbrain.notefile import NoteMeta
    meta = NoteMeta(capture_id="cap_x", device_id="d", created=datetime(2026, 10, 7, 9, 0, tzinfo=UTC),
                    duration_s=0.4, title="Nota del mattino, 11:00", url=None)
    text = render_note(Note(), ZoneInfo("Europe/Rome"), meta)
    assert "url:" not in text and "title: 'Nota del mattino, 11:00'\n" in text and "duration: 0\n" in text
```

- [ ] **Step 2: Verificare che fallisca**: `ImportError: NoteMeta`.

- [ ] **Step 3: Implementazione** (`notefile.py`):

```python
@dataclass(frozen=True)
class NoteMeta:
    """Identita' della registrazione nel frontmatter (spec vault §5): scritta, mai riletta."""
    capture_id: str
    device_id: str
    created: datetime
    duration_s: float
    title: str          # titolo mostrato (manuale, automatico o di default)
    url: str | None
```

In `render_note(note, tz, meta=None)`: se `meta` è presente, `front` parte da `{"id": meta.capture_id, "device": meta.device_id, "created": meta.created.astimezone(tz).isoformat(timespec="seconds"), "duration": int(round(meta.duration_s))}`, poi `title = meta.title` (al posto di `note.title`, che resta il titolo automatico nel catalogo) e `aliases = [meta.title]`, poi `summary`, `tags`, `url` (se non `None`), poi i campi tecnici come oggi. Senza `meta` il comportamento resta identico (serve ai test esistenti e a `rescan` sulle note vecchie). `parse_note` non cambia (ignora le chiavi sconosciute: verificare che non le rifiuti; se le rifiuta, aggiungere le cinque chiavi alla lista di quelle note).

`notes.py`:

```python
def effective_title(capture: Capture, tz: ZoneInfo) -> str:
    return capture.title or capture.title_auto or default_title(capture.recorded_at, tz)


def note_meta(capture: Capture, tz: ZoneInfo, public_url: str | None) -> NoteMeta:
    return NoteMeta(capture_id=capture.capture_id, device_id=capture.device_id,
                    created=capture.recorded_at, duration_s=capture.duration_s,
                    title=effective_title(capture, tz),
                    url=f"{public_url}/captures/{capture.id}" if public_url else None)
```

`default_title` si sposta da `web/templating.py` a `notes.py` (con le costanti delle fasce); `templating.py` lo importa da lì e `display_title` chiama `effective_title`.

- [ ] **Step 4: Suite verde.**

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/notefile.py backend/secondbrain/notes.py backend/secondbrain/web/templating.py backend/tests/test_notefile.py
git commit -m "backend: frontmatter della nota con identita' della registrazione" -m "id, device, created, duration, title/aliases e url (con PUBLIC_URL) in testa al frontmatter: Obsidian mostra titolo e proprieta', il backend ritrova la nota per id. Il parser ignora i campi nuovi."
```

---

### Task 4: La nota vive nel vault — scrittura, titolo, data, cestino, UI

**Files:**
- Modify: `backend/secondbrain/notes.py` (`write_note`, `expected_note_rel`, `place_note`), `backend/secondbrain/library.py`, `backend/secondbrain/archive.py` (`delete` cancella anche `.md`? No: il `.md` nel cestino dell'archivio è `<base>.md`, già coperto da `related_files`), `backend/secondbrain/worker.py` (passa `vault` e `public_url` a `write_note`), `backend/secondbrain/web/browse.py`, `backend/secondbrain/web/templates/_detail.html`, `backend/secondbrain/web/ai.py` e `actions.py` (nuovi argomenti), `backend/tests/ai_fakes.py` (`processed` scrive nel vault)
- Test: `backend/tests/test_notes.py`, `backend/tests/test_library.py`, `backend/tests/test_browse.py`

**Interfaces:**
- Consumes: `Vault` (Task 2), `NoteMeta`/`note_meta`/`effective_title` (Task 3).
- Produces: `notes.write_note(s, archive, vault, capture, note, tz, public_url)`; `notes.expected_note_rel(vault, capture, tz) -> str` (percorso atteso, con `unique_rel` se il file atteso non è già quello della nota); `notes.place_note(s, vault, capture, tz, public_url)` (rinomina/riscrive il file se nome o frontmatter devono cambiare: usata da titolo e data); `library.trash_capture/restore_capture/delete_capture/purge_trash` con `vault`; rotta `GET /captures/{id}/note`.

- [ ] **Step 1: Test che falliscono**

`tests/test_notes.py` (in coda; usare le fixture `db`, `settings`, `recordings` come negli altri test del file):

```python
def test_write_note_puts_the_file_in_the_vault_and_tracks_it(recordings, db, settings):
    capture = capture_by(db, "cap_20260923_191530")
    vault = Vault(settings.vault_dir)
    note = Note(transcript="Ciao.", title="Chiamare Marco", tags=("lavoro",))
    write_note(db, Archive(settings.archive_dir), vault, capture, note, settings.tz_archive, "https://b.it")
    db.commit()
    assert capture.note_path == "Captures/2026/2026-09-23 21.15 Chiamare Marco.md"
    text = vault.read_text(capture.note_path)
    assert "id: cap_20260923_191530" in text and "url: https://b.it/captures/" in text and text.endswith("Ciao.\n")
    assert capture.note_mtime == vault.mtime(capture.note_path)
    assert not Archive(settings.archive_dir).derived_path(capture.rel_path, ".md").exists()


def test_write_note_renames_when_the_title_changes(recordings, db, settings):
    capture = capture_by(db, "cap_20260923_191530")
    vault = Vault(settings.vault_dir)
    args = (db, Archive(settings.archive_dir), vault, capture)
    write_note(*args, Note(transcript="x", title="Prima"), settings.tz_archive, None)
    write_note(*args, Note(transcript="x", title="Dopo"), settings.tz_archive, None)
    assert capture.note_path == "Captures/2026/2026-09-23 21.15 Dopo.md"
    assert vault.list_notes() and len(vault.list_notes()) == 1
```

`tests/test_library.py` (in coda):

```python
def test_manual_title_and_date_rename_the_vault_note(recordings, db, settings):
    cid = capture_by(db, "cap_20260923_191530").id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    library.set_title(db, Archive(settings.archive_dir), vault, settings.tz_archive, None, cid, "Mio titolo")
    assert capture_by(db, "cap_20260923_191530").note_path == "Captures/2026/2026-09-23 21.15 Mio titolo.md"
    library.correct_recorded_at(db, Archive(settings.archive_dir), vault, settings.tz_archive, None, cid,
                                datetime(2025, 12, 31, 8, 30))
    c = capture_by(db, "cap_20260923_191530")
    assert c.note_path == "Captures/2025/2025-12-31 08.30 Mio titolo.md" and "created: '2025-12-31T08:30:00+01:00'" in vault.read_text(c.note_path)


def test_trash_restore_and_delete_move_the_note(recordings, db, settings):
    cid = capture_by(db, "cap_20260923_191530").id
    processed(db, settings, cid, title="Auto")
    archive, vault = Archive(settings.archive_dir), Vault(settings.vault_dir)
    library.trash_capture(db, archive, vault, settings.tz_archive, None, cid, NOW)
    c = capture_by(db, "cap_20260923_191530")
    assert c.note_path is None and vault.list_notes() == []
    assert archive.derived_path(c.rel_path, ".md").exists()        # nel cestino dell'archivio, accanto al WAV
    library.restore_capture(db, archive, vault, settings.tz_archive, None, cid)
    c = capture_by(db, "cap_20260923_191530")
    assert c.note_path == "Captures/2026/2026-09-23 21.15 Auto.md" and not archive.derived_path(c.rel_path, ".md").exists()
    library.trash_capture(db, archive, vault, settings.tz_archive, None, cid, NOW)
    library.delete_capture(db, archive, vault, cid)
    assert vault.list_notes() == [] and not list((settings.archive_dir / ".trash").rglob("*.md"))
```

`tests/test_browse.py` (in coda):

```python
def test_detail_lists_the_vault_note_and_serves_it(recordings, db, settings):
    cid = capture_by(db, "cap_20260923_191530").id
    processed(db, settings, cid, title="Auto")
    text = recordings.client.get(f"/captures/{cid}").text
    assert "Captures/2026/2026-09-23 21.15 Auto.md" in text and f'href="/captures/{cid}/note"' in text
    r = recordings.client.get(f"/captures/{cid}/note")
    assert r.status_code == 200 and "id: cap_20260923_191530" in r.text
```

- [ ] **Step 2: Verificare che falliscano** (firme nuove → `TypeError`/`AttributeError`).

- [ ] **Step 3: Implementazione**

`notes.py`:

```python
def expected_note_rel(vault: Vault, capture: Capture, tz: ZoneInfo) -> str:
    local = capture.recorded_at.astimezone(tz)
    name = vault_note_name(local, effective_title(capture, tz))
    wanted = f"{note_dir(local)}/{name}"
    if capture.note_path == wanted:
        return wanted
    return vault.unique_rel(note_dir(local), name)


def write_note(s, archive, vault, capture, note, tz, public_url) -> None:
    """Prima il `.md` nel vault (atomico, rinominato se il titolo e' cambiato), poi catalogo e indice."""
    for key, value in note_fields(note).items():
        setattr(capture, key, value)
    _place(vault, capture, note, tz, public_url)
    refresh_search_vector(s, capture)


def place_note(s, vault, capture, tz, public_url) -> None:
    """Titolo manuale o data cambiati: stesso contenuto AI, nome e frontmatter aggiornati."""
    if capture.trashed_at is not None:
        return
    _place(vault, capture, note_from_capture(capture), tz, public_url)


def _place(vault, capture, note, tz, public_url) -> None:
    wanted = expected_note_rel(vault, capture, tz)
    if capture.note_path and capture.note_path != wanted and vault.mtime(capture.note_path) is not None:
        vault.rename(capture.note_path, wanted)
    capture.note_path = wanted
    capture.note_mtime = vault.write_text(wanted, render_note(note, tz, note_meta(capture, tz, public_url)))
```

`archive.write_text(rel, NOTE_SUFFIX, ...)` non viene più chiamata per la nota: `archive` resta nel parametro di `write_note` solo per il `.ai.json` del worker (se non serve, toglierlo e aggiornare i chiamanti).

`library.py`: `set_title` e `correct_recorded_at` prendono `vault, tz, public_url` e chiamano `notes.place_note` prima di `_save`. `trash_capture`: dopo `archive.move`, se `capture.note_path`: `shutil.move(vault.abs(note_path), archive.derived_path(capture.rel_path, NOTE_SUFFIX))`, `note_path = None`, `note_mtime = None`. `restore_capture`: dopo `archive.move`, se esiste `archive.derived_path(rel_path, NOTE_SUFFIX)`: leggerlo, cancellarlo e `notes.place_note(...)` riscrive nel vault (il contenuto AI viene dal catalogo, che è allineato). `delete_capture` e `purge_trash`: `archive.delete` già cancella `<base>.*` compreso il `.md` nel cestino; se `note_path` non è `None` (non dovrebbe), `vault.delete(note_path)`. `edit_ai_field` e `reprocess`: passano `vault`/`public_url` a `write_note`.

`worker.py`: `Worker` acquista `vault: Vault` e `public_url: str | None`; le chiamate a `notes.write_note` passano i nuovi argomenti; `cli._worker` e i test costruiscono il worker con `Vault(settings.vault_dir)`.

`web/browse.py`: nel dettaglio `"note_path": capture.note_path`; rotta:

```python
@router.get("/captures/{capture_id}/note")
def capture_note(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    if not capture.note_path:
        raise HTTPException(404, "nota non presente nel vault")
    path = request.app.state.vault.abs(capture.note_path)
    if not path.is_file():
        raise HTTPException(404, "nota non trovata sul disco")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", filename=path.name)
```

`_detail.html`, nella voce File, prima dei file dell'archivio: `{% if note_path %}<li><a href="/captures/{{ c.id }}/note" aria-label="Scarica la nota" title="Scarica la nota">{{ icon("download") }}{{ note_path }}</a></li>{% endif %}`. Le rotte di `actions.py` e `ai.py` passano `request.app.state.vault`, `settings.tz_archive`, `settings.public_url`. `tests/ai_fakes.processed` scrive con il vault di `settings`.

- [ ] **Step 4: Suite verde**; screenshot del dettaglio (chiaro, scuro, 390 px) con la riga della nota.

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain backend/tests
git commit -m "backend: la nota vive nel vault" -m "write_note scrive il .md in Captures/<anno>/<data> <ora> <titolo>.md e lo rinomina quando titolo o data cambiano; cestino e ripristino spostano la nota tra vault e cestino dell'archivio; il dettaglio elenca la nota con il percorso nel vault e la serve da /captures/<id>/note. UI invariata nell'uso."
```

---

### Task 5: `vault_sync` — modifiche da Obsidian riprese dal worker

**Files:**
- Create: `backend/secondbrain/vault_sync.py`
- Modify: `backend/secondbrain/worker.py` (chiamata periodica), `backend/secondbrain/settings_store.py` (stato dell'ultimo controllo: chiave `vault_check`, valore `{"at", "notes", "problems"}`), `backend/secondbrain/web/settings.py`, `backend/secondbrain/web/templates/settings.html`
- Test: `backend/tests/test_vault_sync.py` (nuovo), `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: Task 2-4.
- Produces: `vault_sync.check(sessionmaker, vault, tz, public_url, now) -> VaultReport(changed: int, problems: list[str])`; `Worker.maybe_check_vault()`; costante `VAULT_CHECK_INTERVAL_S = 60`.

- [ ] **Step 1: Test che falliscono** (`tests/test_vault_sync.py`):

```python
import os
from secondbrain import vault_sync
from secondbrain.vault import Vault
from tests.ai_fakes import processed
from tests.helpers import NOW, capture_by

CID = "cap_20260923_191530"


def edited_in_obsidian(vault, rel, old, new):
    path = vault.abs(rel)
    path.write_text(path.read_text().replace(old, new))
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 5))   # Syncthing conserva l'mtime del mittente: basta che sia diverso


def test_changed_body_and_tags_become_manual_edits(recordings, db, settings, sessionmaker):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto", transcript="Vecchio testo.", tags=("casa",))
    vault = Vault(settings.vault_dir)
    rel = capture_by(db, CID).note_path
    edited_in_obsidian(vault, rel, "Vecchio testo.", "Nuovo testo.")
    edited_in_obsidian(vault, rel, "tags: [casa]", "tags: [casa, giardino]")
    report = vault_sync.check(sessionmaker, vault, settings.tz_archive, None, NOW)
    db.expire_all()
    c = capture_by(db, CID)
    assert report.changed == 1 and c.transcript == "Nuovo testo." and c.tags == ["casa", "giardino"]
    assert set(c.edited) == {"transcript", "tags"} and c.note_mtime == vault.mtime(rel)
    assert vault_sync.check(sessionmaker, vault, settings.tz_archive, None, NOW).changed == 0   # idempotente


def test_renamed_file_sets_the_manual_title_and_is_normalised(recordings, db, settings, sessionmaker):
    cid = capture_by(db, CID).id
    processed(db, settings, cid, title="Auto")
    vault = Vault(settings.vault_dir)
    old = capture_by(db, CID).note_path
    vault.rename(old, "Captures/2026/2026-09-23 21.15 Titolo mio.md")
    vault_sync.check(sessionmaker, vault, settings.tz_archive, None, NOW)
    db.expire_all()
    c = capture_by(db, CID)
    assert c.title == "Titolo mio" and c.note_path == "Captures/2026/2026-09-23 21.15 Titolo mio.md"


def test_unknown_id_temporaries_and_conflicts_are_reported_not_applied(recordings, db, settings, sessionmaker):
    vault = Vault(settings.vault_dir)
    vault.write_text("Captures/2026/2026-01-01 10.00 Estranea.md", "---\nid: cap_20260101_100000\n---\nx\n")
    vault.write_text("Captures/2026/2026-01-01 10.00 Senza id.md", "---\ntitle: x\n---\nx\n")
    vault.write_text("Captures/2026/2026-01-01 10.00 Rotta.md", "---\nid: [\n---\n")
    report = vault_sync.check(sessionmaker, vault, settings.tz_archive, None, NOW)
    assert report.changed == 0 and len(report.problems) == 3


def test_running_job_does_not_lose_the_obsidian_edit(recordings, db, settings, sessionmaker):
    # Il lock di riga serializza: dopo check, edited contiene transcript e il worker
    # (che rilegge edited prima di scrivere) non lo sovrascrive. Verifica con jobs.claim
    # + vault_sync.check + notes.write_note con apply_enrichment: il transcript resta quello di Obsidian.
    ...
```

(Completare l'ultimo test con le funzioni già usate in `tests/test_worker.py` per la protezione di `edited`.) In `tests/test_worker.py`: `test_worker_checks_the_vault_when_idle`: con `Worker.maybe_check_vault()` chiamato due volte entro 60 s il controllo gira una volta (usa `clock`).

- [ ] **Step 2: Verificare che falliscano**: `ImportError: vault_sync`.

- [ ] **Step 3: Implementazione** (`vault_sync.py`):

```python
"""Modifiche fatte in Obsidian riprese dal vault (spec vault §7)."""
import logging
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .models import Capture
from .notefile import NoteError, apply_edit, parse_note
from .notes import lock_capture, note_from_capture, write_note_from_vault
from .vault import Vault, clean_title_for_name

log = logging.getLogger(__name__)
FIELDS = ("transcript", "summary", "tags")


@dataclass
class VaultReport:
    changed: int = 0
    problems: list[str] = field(default_factory=list)


def _capture_id_of(text: str) -> str | None: ...   # legge `id:` dal frontmatter con parse_note / yaml


def check(sessionmaker, vault: Vault, tz: ZoneInfo, public_url: str | None, now: datetime) -> VaultReport:
    report = VaultReport()
    for rel in vault.conflicts():
        report.problems.append(f"{rel}: file di conflitto di Syncthing, ignorato")
    with sessionmaker() as s:
        known = {c.note_path: (c.id, c.note_mtime) for c in s.execute(
            select(Capture.id, Capture.note_path, Capture.note_mtime).where(Capture.note_path.is_not(None)))}
        for rel, mtime in vault.list_notes():
            if rel in known and known[rel][1] == mtime:
                continue
            _apply(s, vault, rel, mtime, tz, public_url, report)
            s.commit()
    return report
```

`_apply`: legge il testo, `parse_note` (su `NoteError` → problem), prende `id` (assente → problem), trova la riga per `capture_id` (assente → problem), `lock_capture`; se `rel != capture.note_path`: file rinominato → titolo dalla parte dopo l'ora del nome (`rel` senza cartella, senza i primi 17 caratteri `AAAA-MM-GG HH.MM `, senza `.md`), se diversa da `clean_title_for_name(effective_title)` → `capture.title = quella parte`; poi `capture.note_path = rel` (il vecchio file non esiste più). Per ogni campo in `FIELDS` diverso dal catalogo (`tags` come tuple normalizzata, `transcript`/`summary` strip): `note = apply_edit(note, field, valore_del_file)`; `title` del file diverso da `effective_title` → `capture.title`. Se qualcosa è cambiato o il nome non è quello atteso: `write_note(...)` (riscrive normalizzato, rinomina se serve) e `report.changed += 1`; altrimenti solo `capture.note_mtime = mtime`. Ogni problema va anche in `log.warning` una volta (tenere un set dei `rel` segnalati a livello di modulo con il loro mtime).

`worker.py`: `Worker` acquista `_last_vault_check: datetime | None`; `maybe_check_vault()`: se `now - _last >= VAULT_CHECK_INTERVAL_S`: `report = vault_sync.check(...)` dentro `try/except OSError` (log), poi `store.set_setting(s, "vault_check", {"at": now.isoformat(), "notes": len(vault.list_notes()), "problems": report.problems[:20]})`. `run_forever`: quando `busy` è `False`, prima di `stop.wait`, `worker.maybe_check_vault()`.

`web/settings.py` + `settings.html`: sezione "Vault" con `VAULT_DIR`, numero di note, ora dell'ultimo controllo, elenco dei problemi (se ci sono). Screenshot.

- [ ] **Step 4: Suite verde.**

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/vault_sync.py backend/secondbrain/worker.py backend/secondbrain/settings_store.py backend/secondbrain/web/settings.py backend/secondbrain/web/templates/settings.html backend/tests/test_vault_sync.py backend/tests/test_worker.py
git commit -m "backend: modifiche fatte in Obsidian riprese dal worker" -m "Ogni minuto, quando e' libero, il worker rilegge i file del vault con data di modifica diversa da quella a catalogo: corpo, riassunto, tag e titolo cambiati diventano correzioni a mano (edited), un file rinominato da' il titolo manuale; file senza id, sconosciuti, rotti o di conflitto sono segnalati e lasciati stare. Stato del vault nella pagina Impostazioni."
```

---

### Task 6: Migrazione delle note esistenti e `rescan` con il vault

**Files:**
- Create: `backend/secondbrain/vault_migrate.py`
- Modify: `backend/secondbrain/cli.py` (`vault migrate`), `backend/secondbrain/rescan.py`
- Test: `backend/tests/test_vault_migrate.py` (nuovo), `backend/tests/test_rescan.py`

**Interfaces:**
- Produces: `vault_migrate.migrate(s, archive, vault, tz, public_url) -> int` (note spostate); comando `secondbrain vault migrate`; `rescan` ricollega `note_path`/`note_mtime` per `id` e segnala i `.md` ancora nell'archivio.

- [ ] **Step 1: Test che falliscono**

`tests/test_vault_migrate.py`:

```python
def test_migrate_moves_archive_notes_into_the_vault_once(recordings, db, settings):
    cid = capture_by(db, CID).id
    archive, vault = Archive(settings.archive_dir), Vault(settings.vault_dir)
    capture = capture_by(db, CID)
    archive.write_text(capture.rel_path, ".md", render_note(Note(transcript="Vecchia.", title="Vecchia nota"), settings.tz_archive))
    capture.note_path = None; db.commit()
    assert vault_migrate.migrate(db, archive, vault, settings.tz_archive, None) == 1
    c = capture_by(db, CID)
    assert c.note_path == "Captures/2026/2026-09-23 21.15 Vecchia nota.md" and c.transcript == "Vecchia."
    assert not archive.derived_path(c.rel_path, ".md").exists()
    assert vault_migrate.migrate(db, archive, vault, settings.tz_archive, None) == 0
```

`tests/test_rescan.py` (in coda): dopo `processed(...)`, azzerare `note_path`/`note_mtime` a mano nel DB, lanciare `rescan.run(...)` e verificare che `note_path` torni quello del file (ricollegato per `id`) e che i campi AI vengano dal file del vault; con un `.md` lasciato nell'archivio → `report.problems` contiene "da migrare".

- [ ] **Step 2: Verificare che falliscano.**

- [ ] **Step 3: Implementazione**

`vault_migrate.py`: per ogni `Capture` fuori dal cestino con `archive.derived_path(rel_path, ".md")` esistente: `lock_capture`, `parse_note` (su errore → log e salta), `notes.write_note(s, archive, vault, capture, note, tz, public_url)`, poi cancella il `.md` dall'archivio; commit per nota; ritorna il conteggio. `cli.py`: gruppo `vault` con `migrate` (stampa "Spostate N note nel vault").

`rescan.py`: dopo la ricostruzione dall'archivio, `_link_vault(s, vault, report)`: indice `capture_id → file` leggendo `id` da ogni file di `vault.list_notes()` (problemi per file illeggibili/senza id); per ogni riga fuori dal cestino: se c'è un file → `note_path`, `note_mtime` e campi AI dal file (`note_fields(parse_note(...))`), altrimenti `note_path = None` e in coda come oggi; un `.md` nell'archivio fuori dal cestino → problema "`<rel>`: nota ancora nell'archivio, da migrare con `secondbrain vault migrate`" (i campi vengono comunque letti da lì se manca il file nel vault, così il comportamento di oggi resta finché non si migra). `rescan.run` prende `vault`.

- [ ] **Step 4: Suite verde.**

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/vault_migrate.py backend/secondbrain/cli.py backend/secondbrain/rescan.py backend/tests/test_vault_migrate.py backend/tests/test_rescan.py
git commit -m "backend: vault migrate e rescan che ricollega le note del vault" -m "Le note esistenti si spostano dall'archivio al vault una volta sola; rescan ricostruisce note_path e campi AI per id e segnala i .md rimasti nell'archivio."
```

---

### Task 7: Verifica sul Mac con Obsidian e documentazione

**Files:**
- Modify: `backend/README.md` (sezione "Vault Obsidian": variabili, migrazione, cosa si può modificare da Obsidian, Syncthing), `docs/specs/2026-10-07-backend-vault-obsidian-design.md` (Stato), `CLAUDE.md` (Stato, Idee future, Storico)

- [ ] **Step 1: Stack e migrazione**

`cd backend && docker compose up -d --build` (migrazione 0005 all'avvio), `docker compose exec app secondbrain vault migrate` → N note in `data/vault/Captures/2026/`. Aprire Obsidian su `backend/data/vault` (l'autore).

- [ ] **Step 2: Scenari** (l'autore sul device e in Obsidian, il log con `docker compose logs -f worker`):
  1. registrazione dal device → entro pochi secondi la nota compare in Obsidian con titolo, tag, riassunto, `url`;
  2. in Obsidian: modifica del corpo e aggiunta di un tag → entro un minuto la UI mostra il testo nuovo e il tag, con il badge "corretto a mano"; il file viene riscritto normalizzato;
  3. dalla UI: correzione del riassunto → il file in Obsidian cambia (Obsidian ricarica da solo);
  4. in Obsidian: rinomina del file → titolo manuale in UI, file lasciato con il nome scelto;
  5. cestino dalla UI → la nota sparisce da Obsidian; ripristino → torna;
  6. `secondbrain rescan` → 0 problemi, `note_path` invariati.

- [ ] **Step 3: Documentazione e commit**

README backend: sezione nuova; `.env.example` già aggiornato nel Task 1. Stato della spec con data ed esiti. `CLAUDE.md`: Stato (vault sul Mac in `data/vault`, Syncthing da configurare sulla ZimaBoard al deploy), "Idee future" senza la voce delle note nel vault, Storico `2026-10-07 — Note nel vault Obsidian`.

```bash
git add backend/README.md docs/specs/2026-10-07-backend-vault-obsidian-design.md CLAUDE.md
git commit -m "docs: note nel vault Obsidian (README, stato della spec, CLAUDE.md)"
```

---

## Self-Review

- **Copertura**: §4 → Task 1; §5 → Task 2-3; §6 → Task 4; §7 → Task 5; §8 → Task 4 (dettaglio, rotta) e 5 (Impostazioni); §9 → Task 6; §10 → test di ogni task e Task 7.
- **Placeholder**: il quarto test del Task 5 è abbozzato ("...") per rimandare alle funzioni già presenti in `test_worker.py`: va completato in esecuzione, non è un TBD di comportamento (il comportamento è specificato: lock di riga + `edited`).
- **Nomi**: `Vault`, `vault_note_name`, `clean_title_for_name`, `note_dir`, `NoteMeta`, `note_meta`, `effective_title`, `expected_note_rel`, `write_note(s, archive, vault, capture, note, tz, public_url)`, `place_note`, `vault_sync.check`, `VaultReport`, `vault_migrate.migrate`, `note_path`, `note_mtime` coerenti tra i task.
- **Review Focus**: 1-2 → test del Task 2; 3 → quarto test del Task 5; 4 → test del Task 4; 5 → `app.py` nel Task 1 (`ensure` protetto) e `maybe_check_vault` nel Task 5 (`OSError` loggato).
