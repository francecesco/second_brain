# Backend — Elaborazione AI delle note — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ogni nota archiviata viene trascritta e arricchita (titolo, riassunto breve, tag) in automatico da un worker separato che usa provider esterni intercambiabili (Groq, Gemini, OpenAI) con chiavi impostate dalla UI; il risultato sta in `<base>.md` accanto al WAV, si corregge nel finder e si trova con una ricerca full-text.

**Architecture:** L'ingest mette in coda un lavoro (tabella `jobs`) nella stessa transazione della nota; il servizio `worker` (stessa immagine, comando `secondbrain worker`) prende un lavoro alla volta con `FOR UPDATE SKIP LOCKED` e lease, esegue due fasi (`transcribe`, `enrich`) percorrendo la catena principale → riserve, e scrive `.md` e `.ai.json` sul disco e i campi nel catalogo tenendo il lock sulla riga della nota. Logica decisionale in moduli puri testati senza I/O (`notefile`, `languages`, validazione, ripulitura degli errori); gli adattatori parlano HTTP con `httpx` e si testano con `httpx.MockTransport`; il worker si testa con provider finti.

**Tech Stack:** Python 3.12, uv, FastAPI + Starlette, SQLAlchemy 2 + psycopg 3, Alembic, Jinja2 + htmx 2; nuove dipendenze di runtime: `httpx` (≥ 0.27), `cryptography` (≥ 43, Fernet), `soundfile` (≥ 0.12, libsndfile incluso nella wheel, porta `numpy`), `pyyaml` (≥ 6); Postgres 16 (`tsvector`, GIN, `websearch_to_tsquery`, `ts_headline`); Docker compose.

**Spec:** `docs/specs/2026-09-29-backend-elaborazione-ai-design.md` (leggerla prima: i §x citati sotto sono i suoi). Contesto: `docs/specs/2026-09-28-backend-archivio-design.md` e il piano già eseguito `docs/plans/2026-09-28-backend-archivio.md`.

## Global Constraints

- Tutto il codice backend sta in `backend/`. Comandi da `backend/`: `uv sync`, `uv run pytest -q`.
- Branch di lavoro `backend-elaborazione-ai` creato da `master`; il merge in `master` lo decide l'autore.
- I test di integrazione usano un Postgres vero: `docker compose -f docker-compose.test.yml up -d` (porta 55432, dati in tmpfs). Niente SQLite nei test.
- I 252 test esistenti e quelli nuovi devono restare verdi a ogni commit.
- Prima il test che fallisce, poi l'implementazione.
- **Nessun test tocca la rete**: gli adattatori si provano con `httpx.MockTransport`, la catena e il worker con i provider finti di `tests/ai_fakes.py`, la pagina impostazioni sostituendo `app.state.http` con un client su `MockTransport`. Nel repo e nei test ci sono solo chiavi finte (`gsk_test_…`, `AIza-test-…`, `sk-test-…`).
- Nessun numero magico sparso: limiti, durate, priorità, timeout come costanti con nome in cima al modulo o in `config.py`.
- Datetime sempre *aware* in UTC nel codice e nel DB; `processed_at` nel frontmatter del `.md` è scritto nel fuso `TZ_ARCHIVE`.
- File della nota accanto al WAV con lo stesso nome base: `<base>.md` (nota leggibile, verità per i campi AI) e `<base>.ai.json` (risposte grezze). Si scrivono solo con `Archive.write_text` (scrittura atomica via `.incoming/` + rename) e si risolvono solo con `Archive.derived_path`/`Archive.abs`.
- Ordine delle scritture come nel resto del backend: prima il disco, poi il commit. I campi AI di una nota (UI e worker) si scrivono solo tenendo il lock sulla riga (`notes.lock_capture`, `SELECT … FOR UPDATE`) e rileggendo `edited` sotto quel lock.
- **Chiavi API mai nei log, mai negli errori mostrati, mai nell'HTML**: cifrate con Fernet (`SETTINGS_KEY` solo nel `.env`), mostrate solo mascherate (`gsk_…a3f`), Gemini con l'header `x-goog-api-key` (mai nell'URL), ogni messaggio d'errore dei provider passa da `ai.base.redact` prima di finire in `last_error`, nei log o nella pagina. `Settings.settings_key` e `ProviderConfig.api_key` hanno `repr=False`.
- HTTP verso i provider solo con `httpx`, niente SDK dei provider; timeout espliciti per chiamata.
- Default dei modelli (verificati sulla documentazione dei provider il 2026-09-29): Groq base `https://api.groq.com/openai/v1`, trascrizione `whisper-large-v3-turbo`, testo `openai/gpt-oss-120b`, accetta FLAC, 25 MB; OpenAI base `https://api.openai.com/v1`, trascrizione `gpt-4o-mini-transcribe`, testo vuoto (da scegliere), si manda il WAV (FLAC non confermato), 25 MB; Gemini base `https://generativelanguage.googleapis.com/v1beta`, `gemini-3.8-flash` per entrambe le fasi, 20 MB per richiesta inline intera, accetta FLAC. "Prova" = `GET {base}/models` per entrambi i tipi di adattatore.
- Commit piccoli, messaggi in italiano con prefisso `backend:` (codice) o `docs:`, corpo che spiega il perché. **Nessun riferimento all'assistente** (niente `Co-Authored-By`, niente "Generated with") nei commit, nel codice, nei commenti, nei nomi. Autore `francecesco <francecesco78@gmail.com>` (config locale del repo, già impostata; non toccare la config globale).
- `firmware/main/secrets.h` resta fuori da git.
- Le checkbox di questo piano **non** si spuntano: lo stato si legge dai commit e dallo "Stato" della spec.

## Review Focus

Casi che la spec implica e che un uso reale incontrerà; ognuno ha un test nel task indicato.

1. L'utente corregge riassunto o tag dal telefono mentre il worker sta arricchendo la stessa nota → la correzione resta, il campo finisce in `edited` e il worker aggiorna solo titolo e campi non corretti (test in Task 10; il lock di riga che lo rende possibile in Task 9).
2. Chiave Gemini sbagliata o revocata: Gemini risponde `400 INVALID_ARGUMENT` con `reason: API_KEY_INVALID`, non `401` → è un errore del servizio, si passa alla riserva invece di segnare la nota come fallita per "errore di contenuto" (Task 6).
3. Il provider ripete la chiave nel corpo dell'errore, o un'eccezione di rete riporta l'URL → né l'eccezione, né `last_error`, né i log (`caplog`), né la pagina impostazioni la contengono (Task 5, Task 6, Task 14).
4. Backfill di centinaia di note vecchie avviato poco prima di registrare una nota nuova → la nota nuova passa davanti all'arretrato (priorità), non aspetta ore (Task 8).
5. Tempesta di `429` da tutti i provider (quota esaurita): il lavoro torna in coda con backoff 1 min, 5 min, 30 min, 2 h, 6 h, il worker non ritenta a raffica e al sesto tentativo la nota è `failed` con gli errori leggibili di entrambi i provider (Task 10).

Anche coperti: il device che ritenta una nota già elaborata riceve `409` e la nota resta `done` senza un nuovo lavoro (Task 8); il cestino durante l'elaborazione scarta il risultato (Task 10).

---

## File Structure

| File | Responsabilità | Task |
|---|---|---|
| `backend/pyproject.toml`, `uv.lock` | dipendenze `httpx`, `cryptography`, `soundfile`, `pyyaml` | 1 |
| `backend/secondbrain/config.py` | `SETTINGS_KEY` (opzionale, validata, nascosta nel repr) | 1 |
| `backend/secondbrain/models.py` | colonne AI su `Capture`, `Job`, `Setting`, `AiProvider`, `AiUsage` | 1 |
| `backend/migrations/versions/0004_ai_processing.py` | migrazione: colonne, tabelle, indici GIN | 1 |
| `backend/secondbrain/languages.py` (puro) | lingue: nome per i prompt, configurazione della ricerca | 2 |
| `backend/secondbrain/notefile.py` (puro) | formato del `.md`, regole di `edited`, validazione dell'arricchimento | 2 |
| `backend/secondbrain/ai/registry.py` | provider conosciuti, default, `ProviderConfig`, `build_provider` | 3, 6 |
| `backend/secondbrain/settings_store.py` | Fernet, mascheratura, impostazioni, righe dei provider | 3 |
| `backend/secondbrain/ai/base.py` | interfacce, risultati, errori, `redact`, `send`, prompt | 4 |
| `backend/secondbrain/ai/audio.py` | WAV → FLAC, scelta del formato per il provider | 4 |
| `backend/secondbrain/ai/openai_compatible.py` | adattatore Groq/OpenAI | 5 |
| `backend/secondbrain/ai/gemini.py` | adattatore Gemini | 6 |
| `backend/secondbrain/ai/chain.py` | principale → riserve, classificazione degli errori | 7 |
| `backend/secondbrain/usage.py` | minuti e chiamate per provider e mese | 7 |
| `backend/secondbrain/jobs.py` | coda: accodamento, claim con lease, backoff, cestino, backfill | 8 |
| `backend/secondbrain/ingest.py` | lavoro creato nella transazione della nota | 8 |
| `backend/secondbrain/archive.py` | `write_text`, `read_text`, `derived_path`, suffissi `.md`/`.ai.json` | 9 |
| `backend/secondbrain/search.py` | aggiornamento di `search_vector`, ricerca, evidenziazione | 9, 13 |
| `backend/secondbrain/notes.py` | Note ↔ catalogo, lock di riga, scrittura `.md` + catalogo | 9 |
| `backend/secondbrain/library.py` | lock di riga, correzione dei campi, Rielabora, cestino che annulla il lavoro | 8, 9 |
| `backend/secondbrain/worker.py` | ciclo del worker, due fasi, backoff, arresto pulito | 10 |
| `backend/secondbrain/rescan.py` | lettura dei `.md`, rimessa in coda delle note senza `.md` | 11 |
| `backend/secondbrain/cli.py` | `gen-key`, `process`, `worker`, `rescan` con il conteggio | 3, 8, 10, 11 |
| `backend/secondbrain/catalog.py` | lista del giorno con il lavoro precaricato | 12 |
| `backend/secondbrain/web/ai.py` | stato della riga, modifica inline, Rielabora | 12 |
| `backend/secondbrain/web/search.py` | pagina `/search` | 13 |
| `backend/secondbrain/web/settings.py` | pagina `/settings` | 14 |
| `backend/secondbrain/web/context.py`, `templating.py`, `browse.py`, `app.py` | avviso nel finder, titolo mostrato, apertura della nota, router | 12, 13, 14 |
| `backend/secondbrain/web/templates/*.html`, `static/style.css` | righe, dettaglio, ricerca, impostazioni, icone | 12, 13, 14 |
| `backend/docker-compose.yml`, `.env.example`, `README.md` | servizio `worker`, `SETTINGS_KEY`, istruzioni e privacy | 15 |
| `backend/tests/ai_fakes.py` | transport e provider finti, provider di test configurati, nota elaborata | 5, 7, 10, 12 |
| `backend/tests/…` | test | tutti |

---

### Task 1: Dipendenze, `SETTINGS_KEY`, migrazione 0004 e modelli

**Files:**
- Modify: `backend/pyproject.toml`, `backend/uv.lock` (con `uv add`)
- Modify: `backend/secondbrain/config.py`
- Modify: `backend/secondbrain/models.py`
- Create: `backend/migrations/versions/0004_ai_processing.py`
- Modify: `backend/tests/helpers.py` (`TEST_SETTINGS_KEY`), `backend/tests/conftest.py` (fixture `settings`)
- Test: `backend/tests/test_config.py`, `backend/tests/test_models_ai.py`

**Interfaces:**
- Consumes: `Settings`, `load_settings`, `ConfigError` (config esistente); `Base`, `Capture` (modelli esistenti).
- Produces:
  - `Settings.settings_key: str | None` (default `None`, `repr=False`), letto da `SETTINGS_KEY`; valore non vuoto ma non valido per Fernet → `ConfigError` che nomina `SETTINGS_KEY`.
  - Colonne nuove su `Capture`: `transcript: str | None`, `title_auto: str | None` (200), `summary: str | None`, `tags: list[str]` (non null, default `[]`), `edited: list[str]` (non null, default `[]`), `language: str | None` (8), `ai_provider: str | None`, `ai_transcribe_model: str | None`, `ai_enrich_provider: str | None`, `ai_enrich_model: str | None`, `processed_at: datetime | None`, `search_vector` (TSVECTOR, deferred); relazione in sola lettura `Capture.job: Job | None`.
  - `Job` (`jobs`): `capture_id: uuid.UUID` (PK, FK `captures.id` `ON DELETE CASCADE`), `status: str`, `stage: str`, `priority: int`, `attempts: int`, `next_run_at: datetime`, `locked_until: datetime | None`, `last_error: str | None`, `updated_at: datetime`.
  - `Setting` (`settings`): `key: str` (PK), `value: Any` (JSONB).
  - `AiProvider` (`ai_providers`): `name: str` (PK), `enabled: bool`, `position: int`, `api_key_enc: str | None`, `transcribe_model: str`, `text_model: str`, `updated_at: datetime`.
  - `AiUsage` (`ai_usage`): PK (`provider: str`, `month: date`), `audio_seconds: float`, `calls: int`.
  - `tests.helpers.TEST_SETTINGS_KEY: str` (chiave Fernet fissa, solo per i test); la fixture `settings` la imposta.

- [ ] **Step 1: Dipendenze**

Run:
```bash
cd backend
git switch -c backend-elaborazione-ai
uv add "httpx>=0.27" "cryptography>=43" "soundfile>=0.12" "pyyaml>=6"
uv run python -c "import httpx, cryptography, soundfile, yaml, numpy; print(soundfile.__libsndfile_version__)"
```
Expected: `pyproject.toml` ha le quattro righe nuove in `dependencies` (httpx resta anche nel gruppo `dev`, va bene), `uv.lock` aggiornato con `soundfile`, `numpy`, `cffi`, `cryptography`; stampa la versione di libsndfile (es. `1.2.2`). La sezione `dependencies` diventa:

```toml
dependencies = [
  "fastapi>=0.115.6,<1",
  "starlette>=0.46",
  "uvicorn[standard]>=0.30",
  "sqlalchemy>=2.0.30,<3",
  "psycopg[binary]>=3.2,<4",
  "alembic>=1.13,<2",
  "jinja2>=3.1,<4",
  "python-multipart>=0.0.9",
  "argon2-cffi>=23.1",
  "tzdata>=2024.1",
  "httpx>=0.27",
  "cryptography>=43",
  "soundfile>=0.12",
  "pyyaml>=6",
]
```

(L'ordine e i limiti superiori esatti li scrive `uv add`; conta che ci siano le quattro dipendenze.)

- [ ] **Step 2: Test della configurazione (falliscono)**

In `backend/tests/test_config.py` aggiungere in cima `from cryptography.fernet import Fernet`, in `test_defaults` la riga `assert s.settings_key is None`, e in fondo:

```python
def test_settings_key_is_optional_and_hidden():
    key = Fernet.generate_key().decode()
    s = load_settings(BASE | {"SETTINGS_KEY": f" {key} "})
    assert s.settings_key == key
    assert key not in repr(s)
    assert load_settings(BASE | {"SETTINGS_KEY": "   "}).settings_key is None


@pytest.mark.parametrize("raw", ["corta", "x" * 44])
def test_bad_settings_key(raw):
    with pytest.raises(ConfigError, match="SETTINGS_KEY"):
        load_settings(BASE | {"SETTINGS_KEY": raw})
```

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL (`Settings` non ha `settings_key`).

- [ ] **Step 3: `config.py`**

In `backend/secondbrain/config.py`:

- import: `from dataclasses import dataclass, field` al posto di `from dataclasses import dataclass`, e `from cryptography.fernet import Fernet`;
- in `Settings`, dopo `device_hostname`:

```python
    # Chiave Fernet per le chiavi API dei provider (spec AI §8): mai nel repr, quindi mai
    # nei log anche se qualcuno stampa le impostazioni.
    settings_key: str | None = field(default=None, repr=False)
```

- prima di `load_settings`:

```python
def _settings_key(env: Mapping[str, str]) -> str | None:
    raw = env.get("SETTINGS_KEY", "").strip()
    if not raw:
        return None
    try:
        Fernet(raw.encode())
    except (ValueError, TypeError):
        raise ConfigError("SETTINGS_KEY non valida: generane una con 'secondbrain gen-key'") from None
    return raw
```

- in `load_settings`, nel costruttore di `Settings`, dopo `device_hostname=...`: `settings_key=_settings_key(env),`.

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 4: Test dei modelli e della migrazione (falliscono)**

In `backend/tests/helpers.py`, dopo `PASSWORD`:

```python
# Chiave Fernet fissa solo per i test: 32 byte in base64 url-safe.
TEST_SETTINGS_KEY = "c2Vjb25kYnJhaW4tdGVzdC1rZXktMzItYnl0ZXMhISE="
```

In `backend/tests/conftest.py`: importare `TEST_SETTINGS_KEY` da `tests.helpers` e cambiare la fixture `settings`:

```python
@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=TEST_DB, archive_dir=tmp_path / "archive",
                    firmware_dir=tmp_path / "firmware", tz_archive=ZoneInfo("Europe/Rome"),
                    settings_key=TEST_SETTINGS_KEY)
```

`backend/tests/test_models_ai.py`:

```python
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import func, inspect, select, text

from secondbrain.models import Capture, Job
from tests.helpers import NOW, TEST_DB, make_capture, make_device

BACKEND_DIR = Path(__file__).resolve().parents[1]


def test_new_capture_columns_have_defaults(db):
    db.add(make_device())
    db.flush()  # niente relazione Device-Capture: l'ordine degli INSERT lo decide il flush
    cap = make_capture()
    db.add(cap)
    db.commit()
    db.expire_all()
    c = db.get(Capture, cap.id)
    assert (c.tags, c.edited, c.transcript, c.title_auto, c.summary, c.job) == ([], [], None, None, None, None)


def test_job_goes_away_with_its_capture(db):
    db.add(make_device())
    db.flush()  # niente relazione Device-Capture: l'ordine degli INSERT lo decide il flush
    cap = make_capture()
    db.add(cap)
    db.flush()
    db.add(Job(capture_id=cap.id, status="queued", stage="transcribe", priority=100, attempts=0,
               next_run_at=NOW, locked_until=None, last_error=None, updated_at=NOW))
    db.commit()
    db.expire_all()
    assert db.get(Capture, cap.id).job.status == "queued"
    db.delete(db.get(Capture, cap.id))
    db.commit()
    assert db.scalar(select(func.count()).select_from(Job)) == 0


def test_search_and_tag_indexes_are_gin(db):
    rows = dict(db.execute(text(
        "select indexname, indexdef from pg_indexes where tablename = 'captures'")).all())
    assert "USING gin" in rows["ix_captures_search_vector"]
    assert "USING gin" in rows["ix_captures_tags"]


def test_migration_0004_downgrades_and_upgrades(engine):
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "0003")
    tables = set(inspect(engine).get_table_names())
    assert not tables & {"jobs", "settings", "ai_providers", "ai_usage"}
    command.upgrade(cfg, "head")
    assert {"jobs", "settings", "ai_providers", "ai_usage"} <= set(inspect(engine).get_table_names())
```

Run: `uv run pytest tests/test_models_ai.py -q`
Expected: FAIL (`ImportError: cannot import name 'Job'`).

- [ ] **Step 5: Modelli**

`backend/secondbrain/models.py` (file completo):

```python
"""Modelli SQLAlchemy del catalogo (spec archivio §8, spec elaborazione AI §7)."""
import uuid
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Double, ForeignKey, Index,
                        Integer, String, Text, UniqueConstraint, Uuid, text)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMPTY_ARRAY = text("'{}'")


class Base(DeclarativeBase):
    pass


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    type: Mapped[str] = mapped_column(String(32))
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_firmware: Mapped[str | None] = mapped_column(String(32))
    last_battery_pct: Mapped[int | None] = mapped_column(Integer)
    last_battery_v: Mapped[float | None] = mapped_column(Double)
    last_power_source: Mapped[str | None] = mapped_column(String(16))


class Capture(Base):
    __tablename__ = "captures"
    __table_args__ = (
        Index("ix_captures_device_capture", "device_id", "capture_id"),
        Index("ix_captures_recorded_at", "recorded_at"),
        Index("ix_captures_day", "day"),
        Index("ix_captures_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_captures_tags", "tags", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(32), ForeignKey("devices.id"))
    capture_id: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    date_estimated: Mapped[bool] = mapped_column(Boolean)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    day: Mapped[date] = mapped_column(Date)
    rel_path: Mapped[str] = mapped_column(String(255), unique=True)
    title: Mapped[str | None] = mapped_column(String(200))
    duration_s: Mapped[float] = mapped_column(Double)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    firmware_version: Mapped[str | None] = mapped_column(String(32))
    battery_pct: Mapped[int | None] = mapped_column(Integer)
    battery_v: Mapped[float | None] = mapped_column(Double)
    power_source: Mapped[str | None] = mapped_column(String(16))
    trashed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Campi AI (spec AI §6-7): la verità è il `.md` accanto al WAV, qui sono una copia
    # per la UI e la ricerca, ricostruibile con `rescan`.
    transcript: Mapped[str | None] = mapped_column(Text)
    title_auto: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list,
                                            server_default=EMPTY_ARRAY)
    edited: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list,
                                              server_default=EMPTY_ARRAY)
    language: Mapped[str | None] = mapped_column(String(8))
    ai_provider: Mapped[str | None] = mapped_column(String(32))
    ai_transcribe_model: Mapped[str | None] = mapped_column(String(100))
    ai_enrich_provider: Mapped[str | None] = mapped_column(String(32))
    ai_enrich_model: Mapped[str | None] = mapped_column(String(100))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search_vector: Mapped[str | None] = mapped_column(TSVECTOR, deferred=True)
    # Sola lettura: il lavoro si cancella con la nota grazie a ON DELETE CASCADE nel DB.
    job: Mapped[Optional["Job"]] = relationship(viewonly=True, uselist=False)


class Job(Base):
    """Un lavoro di elaborazione per nota (spec AI §7)."""
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "priority", "next_run_at"),)

    capture_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("captures.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    stage: Mapped[str] = mapped_column(String(16))
    priority: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Setting(Base):
    """Impostazioni dell'elaborazione: chiave → valore JSON (spec AI §8)."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)


class AiProvider(Base):
    __tablename__ = "ai_providers"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean)
    position: Mapped[int] = mapped_column(Integer)
    api_key_enc: Mapped[str | None] = mapped_column(Text)  # token Fernet, mai la chiave in chiaro
    transcribe_model: Mapped[str] = mapped_column(String(100))
    text_model: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AiUsage(Base):
    """Utilizzo per provider e mese: secondi di audio trascritti e chiamate."""
    __tablename__ = "ai_usage"

    provider: Mapped[str] = mapped_column(String(32), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    audio_seconds: Mapped[float] = mapped_column(Double, default=0.0)
    calls: Mapped[int] = mapped_column(Integer, default=0)


class FirmwareRelease(Base):
    __tablename__ = "firmware_releases"
    __table_args__ = (UniqueConstraint("type", "version", name="uq_firmware_type_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(32))
    file: Mapped[str] = mapped_column(String(128))
    sha256: Mapped[str] = mapped_column(String(64))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    current: Mapped[bool] = mapped_column(Boolean)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WebSession(Base):
    __tablename__ = "web_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_at", "at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ok: Mapped[bool] = mapped_column(Boolean)
    ip: Mapped[str | None] = mapped_column(String(64))
```

- [ ] **Step 6: Migrazione 0004**

`backend/migrations/versions/0004_ai_processing.py`:

```python
"""elaborazione AI: campi delle note, coda, impostazioni, utilizzo

Revision ID: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

EMPTY_ARRAY = sa.text("'{}'")
AI_COLUMNS = ("transcript", "title_auto", "summary", "tags", "edited", "language", "ai_provider",
              "ai_transcribe_model", "ai_enrich_provider", "ai_enrich_model", "processed_at",
              "search_vector")


def upgrade() -> None:
    op.add_column("captures", sa.Column("transcript", sa.Text(), nullable=True))
    op.add_column("captures", sa.Column("title_auto", sa.String(200), nullable=True))
    op.add_column("captures", sa.Column("summary", sa.Text(), nullable=True))
    op.add_column("captures", sa.Column("tags", postgresql.ARRAY(sa.String(40)), nullable=False,
                                        server_default=EMPTY_ARRAY))
    op.add_column("captures", sa.Column("edited", postgresql.ARRAY(sa.String(16)), nullable=False,
                                        server_default=EMPTY_ARRAY))
    op.add_column("captures", sa.Column("language", sa.String(8), nullable=True))
    op.add_column("captures", sa.Column("ai_provider", sa.String(32), nullable=True))
    op.add_column("captures", sa.Column("ai_transcribe_model", sa.String(100), nullable=True))
    op.add_column("captures", sa.Column("ai_enrich_provider", sa.String(32), nullable=True))
    op.add_column("captures", sa.Column("ai_enrich_model", sa.String(100), nullable=True))
    op.add_column("captures", sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("captures", sa.Column("search_vector", postgresql.TSVECTOR(), nullable=True))
    op.create_index("ix_captures_search_vector", "captures", ["search_vector"],
                    postgresql_using="gin")
    op.create_index("ix_captures_tags", "captures", ["tags"], postgresql_using="gin")
    # Le note già archiviate hanno solo il titolo manuale; lingua di default = italiano.
    op.execute("UPDATE captures SET search_vector = "
               "setweight(to_tsvector('italian', coalesce(title, '')), 'A')")

    op.create_table(
        "jobs",
        sa.Column("capture_id", sa.Uuid(), sa.ForeignKey("captures.id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_jobs_claim", "jobs", ["status", "priority", "next_run_at"])
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", postgresql.JSONB(), nullable=False),
    )
    op.create_table(
        "ai_providers",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=True),
        sa.Column("transcribe_model", sa.String(100), nullable=False),
        sa.Column("text_model", sa.String(100), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "ai_usage",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("month", sa.Date(), primary_key=True),
        sa.Column("audio_seconds", sa.Double(), nullable=False, server_default="0"),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("ai_usage")
    op.drop_table("ai_providers")
    op.drop_table("settings")
    op.drop_table("jobs")
    op.drop_index("ix_captures_tags", table_name="captures")
    op.drop_index("ix_captures_search_vector", table_name="captures")
    for column in AI_COLUMNS:
        op.drop_column("captures", column)
```

- [ ] **Step 7: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS (252 esistenti + i nuovi).

- [ ] **Step 8: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/secondbrain/config.py backend/secondbrain/models.py backend/migrations/versions/0004_ai_processing.py backend/tests/helpers.py backend/tests/conftest.py backend/tests/test_config.py backend/tests/test_models_ai.py
git commit -m "backend: schema dell'elaborazione AI e SETTINGS_KEY

Migrazione 0004: campi AI sulle note (trascrizione, titolo automatico,
riassunto, tag, campi corretti a mano, provenienza, indice di ricerca GIN),
coda dei lavori con un lavoro per nota che sparisce con la nota, impostazioni,
provider con la chiave cifrata e utilizzo mensile. SETTINGS_KEY è opzionale
(senza, il backend funziona come oggi) ma se c'è deve essere una chiave Fernet
valida, e non compare mai nel repr delle impostazioni."
```

---

### Task 2: `languages` e `notefile` — formato del `.md`, regole di `edited`, validazione (puri)

**Files:**
- Create: `backend/secondbrain/languages.py`
- Create: `backend/secondbrain/notefile.py`
- Test: `backend/tests/test_notefile.py`

**Interfaces:**
- Consumes: niente del backend (moduli puri; `pyyaml` dal Task 1).
- Produces:
  - `languages`: `Language(code, name, ts_config)`, `LANGUAGES: dict[str, Language]` (`it`, `en`, `fr`, `de`, `es`, `pt`), `DEFAULT_LANGUAGE = "it"`, `language_name(code) -> str`, `text_search_config(code: str | None) -> str` (`"it"`/`None` → `"italian"`, sconosciuta → `"simple"`).
  - `notefile`: costanti `EDITABLE_FIELDS = ("transcript", "summary", "tags")`, `MAX_TITLE_LEN = 200`, `MAX_SUMMARY_LEN = 500`, `MAX_TAGS = 8`, `MAX_TAG_LEN = 40`; eccezioni `NoteError(ValueError)`, `EnrichmentInvalid(ValueError)`; dataclass frozen `Note(transcript: str = "", title: str | None, summary: str | None, tags: tuple[str, ...], language, provider, transcribe_model, enrich_provider, enrich_model: str | None, processed_at: datetime | None, edited: tuple[str, ...])` (`title` = titolo automatico); `render_note(note, tz) -> str`, `parse_note(text) -> Note`, `clean_title(str) -> str | None`, `clean_summary(str) -> str | None`, `normalize_tags(Iterable) -> tuple[str, ...]`, `parse_tags_input(str) -> tuple[str, ...]`, `apply_transcript(note, text, *, provider, model, language, at) -> Note`, `apply_enrichment(note, title, summary, tags, *, provider, model, at) -> Note`, `apply_edit(note, field, value) -> Note` (`ValueError` se il campo non è modificabile), `validate_enrichment(data) -> tuple[str, str | None, tuple[str, ...]]`.

Regole fissate qui (spec §6, §9, §10):
- Chiavi del frontmatter in quest'ordine: `title`, `summary`, `tags` (solo se presenti: dopo la sola trascrizione non ci sono), `language`, `provider`, `enrich_provider` (solo se diverso da `provider`: il caso comune resta identico all'esempio della spec), `models` (`{transcribe, enrich}`, solo i presenti), `processed_at` (ISO nel fuso dell'archivio; YAML la mette tra apici), `edited` (sempre).
- `provider` è chi ha trascritto (ha sentito l'audio); le due fasi possono usare provider diversi (spec §5), quindi l'arricchimento ha `enrich_provider`.
- Troppo lungo = troncato; mancante o del tipo sbagliato = `EnrichmentInvalid`. Il titolo diventa una riga sola.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_notefile.py`:

```python
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain.languages import language_name, text_search_config
from secondbrain.notefile import (MAX_SUMMARY_LEN, MAX_TAG_LEN, MAX_TAGS, MAX_TITLE_LEN,
                                  EnrichmentInvalid, Note, NoteError, apply_edit,
                                  apply_enrichment, apply_transcript, parse_note,
                                  parse_tags_input, render_note, validate_enrichment)

ROME = ZoneInfo("Europe/Rome")
AT = datetime(2026, 9, 29, 10, 51, 10, tzinfo=UTC)

SPEC_NOTE = Note(
    transcript="Allora, devo ricordarmi di chiamare Marco…",
    title="Chiamare Marco per il preventivo",
    summary="Ricordarsi di chiamare Marco entro venerdì per il preventivo del tetto.",
    tags=("lavoro", "casa", "telefonate"), language="it", provider="groq",
    transcribe_model="whisper-large-v3-turbo", enrich_provider="groq",
    enrich_model="openai/gpt-oss-120b", processed_at=AT, edited=("tags",))

# L'esempio del §6 della spec; YAML mette tra apici la data per non farla leggere come timestamp.
SPEC_TEXT = """---
title: Chiamare Marco per il preventivo
summary: Ricordarsi di chiamare Marco entro venerdì per il preventivo del tetto.
tags: [lavoro, casa, telefonate]
language: it
provider: groq
models: {transcribe: whisper-large-v3-turbo, enrich: openai/gpt-oss-120b}
processed_at: '2026-09-29T12:51:10+02:00'
edited: [tags]
---

Allora, devo ricordarmi di chiamare Marco…
"""


def test_languages():
    assert text_search_config("it") == "italian"
    assert text_search_config(None) == "italian"
    assert text_search_config("xx") == "simple"
    assert language_name("en") == "inglese" and language_name("xx") == "xx"


def test_render_matches_the_spec_example():
    assert render_note(SPEC_NOTE, ROME) == SPEC_TEXT


def test_round_trip():
    assert parse_note(render_note(SPEC_NOTE, ROME)) == SPEC_NOTE


def test_after_transcription_only_there_is_no_title_summary_tags():
    note = Note(transcript="Ciao", language="it", provider="groq",
                transcribe_model="whisper-large-v3-turbo", processed_at=AT)
    text = render_note(note, ROME)
    assert "title:" not in text and "summary:" not in text and "tags:" not in text
    assert "models: {transcribe: whisper-large-v3-turbo}" in text
    assert parse_note(text) == note


def test_empty_transcript_is_only_frontmatter():
    text = render_note(Note(language="it"), ROME)
    assert text == "---\nlanguage: it\nedited: []\n---\n"
    assert parse_note(text).transcript == ""


def test_enrich_provider_is_written_only_when_different():
    other = Note(transcript="x", provider="groq", transcribe_model="w",
                 enrich_provider="gemini", enrich_model="g")
    assert "enrich_provider: gemini" in render_note(other, ROME)
    assert parse_note(render_note(other, ROME)) == other
    assert "enrich_provider" not in render_note(SPEC_NOTE, ROME)


def test_parse_accepts_hand_edits():
    text = ("---\r\ntitle:   Titolo   scritto a mano  \r\ntags: '#Lavoro'\r\n"
            "processed_at: 2026-09-29T12:51:10+02:00\r\nedited: [tags, sconosciuto]\r\n"
            "---\r\n\r\nTesto corretto.\r\n")
    note = parse_note(text)
    assert note.title == "Titolo scritto a mano"
    assert note.tags == ("lavoro",)
    assert note.processed_at == AT
    assert note.edited == ("tags",)
    assert note.transcript == "Testo corretto."


@pytest.mark.parametrize("text", [
    "senza frontmatter",
    "---\ntitle: x\n",
    "---\ntitle: [non chiusa\n---\n",
    "---\n- una\n- lista\n---\n",
    "---\ntags: {a: 1}\n---\n",
    "---\nmodels: [a]\n---\n",
    "---\ntitle: {a: 1}\n---\n",
    "---\nprocessed_at: ieri\n---\n",
])
def test_parse_errors(text):
    with pytest.raises(NoteError):
        parse_note(text)


def test_transcription_keeps_an_edited_transcript():
    base = Note(transcript="corretto a mano", edited=("transcript",))
    note = apply_transcript(base, " nuovo ", provider="gemini", model="m", language="it", at=AT)
    assert (note.transcript, note.provider, note.transcribe_model, note.processed_at) == (
        "corretto a mano", "gemini", "m", AT)
    fresh = apply_transcript(Note(), " nuovo ", provider="groq", model="w", language="it", at=AT)
    assert fresh.transcript == "nuovo" and fresh.language == "it"


def test_enrichment_keeps_edited_summary_and_tags():
    base = Note(transcript="t", summary="mio", tags=("mio",), edited=("summary", "tags"))
    note = apply_enrichment(base, "Titolo", "auto", ("auto",), provider="groq", model="m", at=AT)
    assert (note.title, note.summary, note.tags) == ("Titolo", "mio", ("mio",))
    note = apply_enrichment(Note(transcript="t"), "Titolo", "auto", ("auto",), provider="groq",
                            model="m", at=AT)
    assert (note.summary, note.tags, note.enrich_provider, note.enrich_model) == (
        "auto", ("auto",), "groq", "m")


def test_edits_mark_the_field_once():
    note = apply_edit(Note(summary="auto"), "summary", "  Mio   riassunto ")
    assert (note.summary, note.edited) == ("Mio riassunto", ("summary",))
    note = apply_edit(note, "tags", "Lavoro, #casa, , lavoro")
    note = apply_edit(note, "summary", "")
    assert (note.tags, note.summary, note.edited) == (("lavoro", "casa"), None, ("summary", "tags"))
    assert apply_edit(Note(), "transcript", " testo ").transcript == "testo"
    with pytest.raises(ValueError):
        apply_edit(Note(), "title", "x")


def test_parse_tags_input():
    assert parse_tags_input("Lavoro, #casa,  , lavoro") == ("lavoro", "casa")
    assert parse_tags_input("") == ()


def test_validate_enrichment_normalizes_and_limits():
    title, summary, tags = validate_enrichment({
        "title": "Riga uno\nriga due " + "x" * 300,
        "summary": "s" * (MAX_SUMMARY_LEN + 100),
        "tags": ["#Lavoro", " CASA ", "lavoro", "y" * 50] + [f"t{i}" for i in range(10)],
    })
    assert title.startswith("Riga uno riga due") and len(title) == MAX_TITLE_LEN
    assert len(summary) == MAX_SUMMARY_LEN
    assert tags[:3] == ("lavoro", "casa", "y" * MAX_TAG_LEN) and len(tags) == MAX_TAGS
    assert validate_enrichment({"title": "T", "summary": "", "tags": []}) == ("T", None, ())


@pytest.mark.parametrize("data", [
    "non un oggetto", ["lista"], {"summary": "s", "tags": []},
    {"title": "   ", "summary": "s", "tags": []}, {"title": "T", "summary": 3, "tags": []},
    {"title": "T", "summary": "s"}, {"title": "T", "summary": "s", "tags": "lavoro"},
    {"title": "T", "summary": "s", "tags": ["ok", 3]},
])
def test_validate_enrichment_rejects_bad_shapes(data):
    with pytest.raises(EnrichmentInvalid):
        validate_enrichment(data)
```

Run: `uv run pytest tests/test_notefile.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'secondbrain.languages'`).

- [ ] **Step 2: `languages`**

`backend/secondbrain/languages.py`:

```python
"""Lingue delle note: nome per i prompt e configurazione della ricerca full-text (spec §8, §10)."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str
    name: str        # nome in italiano, usato nei prompt ai modelli
    ts_config: str   # configurazione di testo di Postgres per stemming e stop word


LANGUAGES = {lang.code: lang for lang in (
    Language("it", "italiano", "italian"),
    Language("en", "inglese", "english"),
    Language("fr", "francese", "french"),
    Language("de", "tedesco", "german"),
    Language("es", "spagnolo", "spanish"),
    Language("pt", "portoghese", "portuguese"),
)}
DEFAULT_LANGUAGE = "it"
FALLBACK_TS_CONFIG = "simple"  # lingua sconosciuta: niente stemming, ma la ricerca funziona


def language_name(code: str) -> str:
    lang = LANGUAGES.get(code)
    return lang.name if lang else code


def text_search_config(code: str | None) -> str:
    """Configurazione di Postgres per la lingua della nota; senza lingua, quella di default."""
    lang = LANGUAGES.get(code or DEFAULT_LANGUAGE)
    return lang.ts_config if lang else FALLBACK_TS_CONFIG
```

- [ ] **Step 3: `notefile`**

`backend/secondbrain/notefile.py`:

```python
"""Nota leggibile `<base>.md` (spec §6) e validazione dell'arricchimento (spec §9): puro.

Il `.md` è la verità per i campi AI: frontmatter YAML con titolo automatico, riassunto,
tag, lingua, provenienza ed `edited` (i campi corretti a mano); il corpo è la trascrizione.
Il titolo manuale non sta qui ma nel sidecar `.json`, come prima dell'elaborazione AI.
"""
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import yaml

EDITABLE_FIELDS = ("transcript", "summary", "tags")
MAX_TITLE_LEN = 200  # come le colonne captures.title e captures.title_auto
MAX_SUMMARY_LEN = 500
MAX_TAGS = 8
MAX_TAG_LEN = 40  # come gli elementi di captures.tags
DELIMITER = "---"
YAML_WIDTH = 10_000  # niente a capo automatici dentro titolo e riassunto


class NoteError(ValueError):
    """`.md` illeggibile: frontmatter mancante, YAML non valido o campi del tipo sbagliato."""


class EnrichmentInvalid(ValueError):
    """Risposta di arricchimento senza i campi richiesti o del tipo sbagliato."""


@dataclass(frozen=True)
class Note:
    transcript: str = ""
    title: str | None = None  # titolo automatico
    summary: str | None = None
    tags: tuple[str, ...] = ()
    language: str | None = None
    provider: str | None = None  # chi ha trascritto (e quindi ha sentito l'audio)
    transcribe_model: str | None = None
    enrich_provider: str | None = None
    enrich_model: str | None = None
    processed_at: datetime | None = None
    edited: tuple[str, ...] = ()


def _one_line(value: str) -> str:
    return " ".join(value.split())


def clean_title(value: str) -> str | None:
    return _one_line(value)[:MAX_TITLE_LEN].rstrip() or None


def clean_summary(value: str) -> str | None:
    return _one_line(value)[:MAX_SUMMARY_LEN].rstrip() or None


def normalize_tags(values: Iterable[object]) -> tuple[str, ...]:
    """Minuscoli, senza `#` e spazi ai bordi, senza doppioni, al massimo MAX_TAGS."""
    tags: list[str] = []
    for raw in values:
        tag = _one_line(str(raw)).lstrip("#").strip().lower()[:MAX_TAG_LEN].rstrip()
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) == MAX_TAGS:
            break
    return tuple(tags)


def parse_tags_input(text: str) -> tuple[str, ...]:
    """Tag scritti a mano nella UI: separati da virgole."""
    return normalize_tags(text.split(","))


def _edited(fields: Iterable[str]) -> tuple[str, ...]:
    wanted = set(fields)
    return tuple(name for name in EDITABLE_FIELDS if name in wanted)


def render_note(note: Note, tz: ZoneInfo) -> str:
    front: dict = {}
    if note.title:
        front["title"] = note.title
    if note.summary:
        front["summary"] = note.summary
    if note.tags:
        front["tags"] = list(note.tags)
    if note.language:
        front["language"] = note.language
    if note.provider:
        front["provider"] = note.provider
    if note.enrich_provider and note.enrich_provider != note.provider:
        front["enrich_provider"] = note.enrich_provider
    models = {key: value for key, value in (("transcribe", note.transcribe_model),
                                            ("enrich", note.enrich_model)) if value}
    if models:
        front["models"] = models
    if note.processed_at:
        front["processed_at"] = note.processed_at.astimezone(tz).isoformat(timespec="seconds")
    front["edited"] = list(note.edited)
    text = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=None,
                          width=YAML_WIDTH)
    head = f"{DELIMITER}\n{text}{DELIMITER}\n"
    body = note.transcript.strip()
    return f"{head}\n{body}\n" if body else head


def _split(text: str) -> tuple[str, str]:
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0].strip() != DELIMITER:
        raise NoteError("frontmatter mancante")
    for i in range(1, len(lines)):
        if lines[i].strip() == DELIMITER:
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    raise NoteError("frontmatter non chiuso")


def _opt_str(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise NoteError(f"{key}: atteso un testo")
    return str(value)


def _list(data: dict, key: str) -> list:
    value = data.get(key)
    if value is None:
        return []
    if isinstance(value, str):
        return [value]  # `tags: lavoro` scritto a mano in Obsidian
    if not isinstance(value, list):
        raise NoteError(f"{key}: attesa una lista")
    return value


def _datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise NoteError("processed_at: data non valida") from None
    else:
        raise NoteError("processed_at: data non valida")
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_note(text: str) -> Note:
    front, body = _split(text)
    try:
        data = yaml.safe_load(front) if front.strip() else {}
    except yaml.YAMLError:
        raise NoteError("frontmatter YAML non valido") from None
    if not isinstance(data, dict):
        raise NoteError("il frontmatter non è una mappa chiave: valore")
    models = data.get("models") or {}
    if not isinstance(models, dict):
        raise NoteError("models: attesa una mappa")
    title = _opt_str(data, "title")
    summary = _opt_str(data, "summary")
    provider = _opt_str(data, "provider")
    enrich_model = _opt_str(models, "enrich")
    return Note(
        transcript=body.strip(),
        title=clean_title(title) if title is not None else None,
        summary=clean_summary(summary) if summary is not None else None,
        tags=normalize_tags(_list(data, "tags")),
        language=_opt_str(data, "language"),
        provider=provider,
        transcribe_model=_opt_str(models, "transcribe"),
        enrich_provider=_opt_str(data, "enrich_provider") or (provider if enrich_model else None),
        enrich_model=enrich_model,
        processed_at=_datetime(data.get("processed_at")),
        edited=_edited(str(name) for name in _list(data, "edited")),
    )


def apply_transcript(note: Note, text: str, *, provider: str, model: str, language: str,
                     at: datetime) -> Note:
    """Risultato della trascrizione; una trascrizione corretta a mano non si tocca."""
    keep = "transcript" in note.edited
    return replace(note, transcript=note.transcript if keep else text.strip(), provider=provider,
                   transcribe_model=model, language=language, processed_at=at)


def apply_enrichment(note: Note, title: str, summary: str | None, tags: tuple[str, ...], *,
                     provider: str, model: str, at: datetime) -> Note:
    """Risultato dell'arricchimento; riassunto e tag corretti a mano restano quelli."""
    return replace(
        note, title=title,
        summary=note.summary if "summary" in note.edited else summary,
        tags=note.tags if "tags" in note.edited else tags,
        enrich_provider=provider, enrich_model=model, processed_at=at)


def apply_edit(note: Note, field: str, value: str) -> Note:
    """Correzione a mano dalla UI: il campo entra in `edited` e il worker non lo tocca più."""
    if field == "transcript":
        cleaned: object = value.strip()
    elif field == "summary":
        cleaned = clean_summary(value)
    elif field == "tags":
        cleaned = parse_tags_input(value)
    else:
        raise ValueError(f"campo non modificabile: {field!r}")
    return replace(note, **{field: cleaned}, edited=_edited((*note.edited, field)))


def validate_enrichment(data: object) -> tuple[str, str | None, tuple[str, ...]]:
    """(titolo, riassunto, tag) normalizzati, o EnrichmentInvalid (spec §9)."""
    if not isinstance(data, dict):
        raise EnrichmentInvalid("la risposta non è un oggetto JSON")
    title, summary, tags = data.get("title"), data.get("summary"), data.get("tags")
    if not isinstance(title, str) or clean_title(title) is None:
        raise EnrichmentInvalid("titolo mancante")
    if not isinstance(summary, str):
        raise EnrichmentInvalid("riassunto mancante")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise EnrichmentInvalid("tag mancanti o non testuali")
    return clean_title(title), clean_summary(summary), normalize_tags(tags)
```

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/languages.py backend/secondbrain/notefile.py backend/tests/test_notefile.py
git commit -m "backend: formato della nota .md e validazione dell'arricchimento

Il .md accanto al WAV è la verità per i campi AI ed è già leggibile da
Obsidian: frontmatter YAML e trascrizione nel corpo. Le regole su cosa il
worker può sovrascrivere (mai i campi in edited) e la normalizzazione di
titolo, riassunto e tag stanno in funzioni pure, così UI, worker e rescan
applicano esattamente le stesse."
```

---

### Task 3: Chiavi e impostazioni — registro dei provider, `settings_store`, `secondbrain gen-key`

**Files:**
- Create: `backend/secondbrain/ai/__init__.py`, `backend/secondbrain/ai/registry.py`
- Create: `backend/secondbrain/settings_store.py`
- Modify: `backend/secondbrain/cli.py` (comando `gen-key`)
- Test: `backend/tests/test_settings_store.py`

**Interfaces:**
- Consumes: `AiProvider`, `Setting` (Task 1); `LANGUAGES`, `DEFAULT_LANGUAGE` (Task 2).
- Produces:
  - `ai.registry`: `KIND_OPENAI_COMPATIBLE`, `KIND_GEMINI`, `ProviderSpec(name, label, kind, base_url, transcribe_model, text_model, audio_formats: tuple[str, ...], max_bytes: int)`, `PROVIDERS` (ordine di default: groq, gemini, openai), `PROVIDER_SPECS: dict[str, ProviderSpec]`, `ProviderConfig(name, transcribe_model, text_model, api_key)` con `api_key` fuori dal repr e proprietà `spec`.
  - `settings_store`: `SecretBox(key: str | None)` con `.available`, `.encrypt(plain) -> str` (`NoSettingsKey` senza chiave), `.decrypt(token) -> str | None`; `generate_key() -> str`; `mask_key(plain) -> str` (`gsk_…a3f`); `get_setting`/`set_setting`; `is_paused(s)`, `set_paused(s, bool)`, `get_language(s) -> str`, `set_language(s, code)` (`ValueError` se sconosciuta); `list_providers(s)`, `ensure_providers(s, now) -> list[AiProvider]`; `save_provider(s, box, name, *, api_key, transcribe_model, text_model, enabled, now)` (chiave vuota = invariata); `clear_api_key(s, name, now)`; `move_provider(s, name, direction, now)` con `MOVE_UP`/`MOVE_DOWN`; `provider_configs(s, box) -> list[ProviderConfig]`; `has_configured_provider(s) -> bool`; `key_status(row, box) -> str` (`NO_KEY`, `UNREADABLE_KEY` o la chiave mascherata); eccezioni `NoSettingsKey`, `UnknownProvider`. Nessuna funzione fa commit.
  - CLI: `secondbrain gen-key` stampa una chiave Fernet nuova (non serve il database).

Scelte: Groq è il principale di default (trascrizione dedicata, economica), Gemini la prima riserva, OpenAI la seconda con il modello di testo vuoto finché non lo si sceglie (spec §4); tutti "abilitati" ma saltati finché non hanno una chiave. La chiave mascherata si calcola decifrando al momento: nel database non resta nemmeno un pezzo della chiave in chiaro.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_settings_store.py`:

```python
import pytest
from cryptography.fernet import Fernet

from secondbrain import settings_store as store
from secondbrain.cli import main
from secondbrain.models import AiProvider
from tests.helpers import NOW, TEST_SETTINGS_KEY

GROQ_KEY = "gsk_test_groq_0123456789abcdef"


@pytest.fixture
def box():
    return store.SecretBox(TEST_SETTINGS_KEY)


def save(db, box, name, api_key, enabled=True, **models):
    spec_models = {"transcribe_model": "m-stt", "text_model": "m-llm"} | models
    return store.save_provider(db, box, name, api_key=api_key, enabled=enabled, now=NOW,
                               **spec_models)


def test_secret_box_round_trip_and_wrong_key(box):
    token = box.encrypt(GROQ_KEY)
    assert GROQ_KEY not in token and box.decrypt(token) == GROQ_KEY
    assert store.SecretBox(Fernet.generate_key().decode()).decrypt(token) is None
    assert box.decrypt("non-un-token") is None


def test_secret_box_without_key():
    box = store.SecretBox(None)
    assert not box.available and box.decrypt("qualcosa") is None
    with pytest.raises(store.NoSettingsKey):
        box.encrypt(GROQ_KEY)


def test_mask_key():
    assert store.mask_key("gsk_abcdefghijklmnopa3f") == "gsk_…a3f"
    assert store.mask_key("corta") == "…"


def test_generate_key_and_cli(capsys):
    Fernet(store.generate_key().encode())
    assert main(["gen-key"]) == 0
    Fernet(capsys.readouterr().out.strip().encode())


def test_default_providers(db):
    rows = store.ensure_providers(db, NOW)
    assert [(r.name, r.transcribe_model, r.text_model, r.enabled, r.api_key_enc) for r in rows] == [
        ("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b", True, None),
        ("gemini", "gemini-3.8-flash", "gemini-3.8-flash", True, None),
        ("openai", "gpt-4o-mini-transcribe", "", True, None),
    ]
    assert store.ensure_providers(db, NOW) == rows


def test_saved_key_is_encrypted_and_masked(db, box):
    store.ensure_providers(db, NOW)
    save(db, box, "groq", f"  {GROQ_KEY} ", transcribe_model=" whisper-large-v3 ")
    db.commit()
    row = db.get(AiProvider, "groq")
    assert GROQ_KEY not in row.api_key_enc and box.decrypt(row.api_key_enc) == GROQ_KEY
    assert row.transcribe_model == "whisper-large-v3"
    assert store.key_status(row, box) == "gsk_…def"
    save(db, box, "groq", "")  # campo vuoto: la chiave resta quella
    assert box.decrypt(db.get(AiProvider, "groq").api_key_enc) == GROQ_KEY
    store.clear_api_key(db, "groq", NOW)
    assert store.key_status(db.get(AiProvider, "groq"), box) == store.NO_KEY


def test_saving_a_key_needs_settings_key(db):
    store.ensure_providers(db, NOW)
    with pytest.raises(store.NoSettingsKey):
        save(db, store.SecretBox(None), "groq", GROQ_KEY)
    save(db, store.SecretBox(None), "groq", "", transcribe_model="altro")
    assert db.get(AiProvider, "groq").transcribe_model == "altro"


def test_provider_configs_skip_disabled_missing_and_unreadable(db, box):
    store.ensure_providers(db, NOW)
    save(db, box, "groq", GROQ_KEY)
    save(db, box, "gemini", "AIza-test-gemini-0123456789", enabled=False)
    db.get(AiProvider, "openai").api_key_enc = store.SecretBox(
        Fernet.generate_key().decode()).encrypt("sk-test-openai-0123456789")
    db.commit()
    configs = store.provider_configs(db, box)
    assert [(c.name, c.api_key, c.transcribe_model) for c in configs] == [("groq", GROQ_KEY, "m-stt")]
    assert GROQ_KEY not in repr(configs[0])
    assert store.key_status(db.get(AiProvider, "openai"), box) == store.UNREADABLE_KEY
    assert store.provider_configs(db, store.SecretBox(None)) == []


def test_has_configured_provider(db, box):
    store.ensure_providers(db, NOW)
    assert not store.has_configured_provider(db)
    save(db, box, "gemini", "AIza-test-gemini-0123456789", enabled=False)
    assert not store.has_configured_provider(db)
    save(db, box, "gemini", "", enabled=True)
    assert store.has_configured_provider(db)


def test_move_provider(db):
    store.ensure_providers(db, NOW)
    store.move_provider(db, "gemini", store.MOVE_UP, NOW)
    assert [r.name for r in store.list_providers(db)] == ["gemini", "groq", "openai"]
    store.move_provider(db, "gemini", store.MOVE_UP, NOW)
    store.move_provider(db, "openai", store.MOVE_DOWN, NOW)
    assert [r.name for r in store.list_providers(db)] == ["gemini", "groq", "openai"]
    with pytest.raises(store.UnknownProvider):
        store.move_provider(db, "acme", store.MOVE_UP, NOW)


def test_unknown_provider(db, box):
    store.ensure_providers(db, NOW)
    with pytest.raises(store.UnknownProvider):
        save(db, box, "acme", GROQ_KEY)


def test_pause_and_language(db):
    assert (store.is_paused(db), store.get_language(db)) == (False, "it")
    store.set_paused(db, True)
    store.set_language(db, "en")
    db.commit()
    assert (store.is_paused(db), store.get_language(db)) == (True, "en")
    with pytest.raises(ValueError):
        store.set_language(db, "xx")
```

Run: `uv run pytest tests/test_settings_store.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'secondbrain.settings_store'`).

- [ ] **Step 2: Registro dei provider**

`backend/secondbrain/ai/__init__.py`:

```python
"""Elaborazione AI delle note: provider, audio, catena di fallback (spec §4, §5)."""
```

`backend/secondbrain/ai/registry.py`:

```python
"""Provider conosciuti con i default dei modelli (spec §4) e la configurazione in uso.

Default verificati sulla documentazione dei provider il 2026-09-29; si cambiano dalla
pagina impostazioni e si controllano col pulsante "Prova".
"""
from dataclasses import dataclass, field

KIND_OPENAI_COMPATIBLE = "openai_compatible"
KIND_GEMINI = "gemini"
MB = 1000 * 1000  # i provider dichiarano i limiti in MB decimali: così si resta sotto
GROQ_MAX_AUDIO_BYTES = 25 * MB
OPENAI_MAX_AUDIO_BYTES = 25 * MB
GEMINI_MAX_REQUEST_BYTES = 20 * MB  # richiesta inline intera, audio in base64 compreso


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    kind: str
    base_url: str
    transcribe_model: str
    text_model: str
    audio_formats: tuple[str, ...]  # formati di ai.audio, in ordine di preferenza
    max_bytes: int                  # file audio (OpenAI compatibile) o richiesta intera (Gemini)


PROVIDERS = (
    ProviderSpec("groq", "Groq", KIND_OPENAI_COMPATIBLE, "https://api.groq.com/openai/v1",
                 "whisper-large-v3-turbo", "openai/gpt-oss-120b", ("flac", "wav"),
                 GROQ_MAX_AUDIO_BYTES),
    ProviderSpec("gemini", "Gemini", KIND_GEMINI,
                 "https://generativelanguage.googleapis.com/v1beta",
                 "gemini-3.8-flash", "gemini-3.8-flash", ("flac", "wav"),
                 GEMINI_MAX_REQUEST_BYTES),
    # FLAC non confermato per OpenAI: si manda il WAV. Modello di testo da scegliere.
    ProviderSpec("openai", "OpenAI", KIND_OPENAI_COMPATIBLE, "https://api.openai.com/v1",
                 "gpt-4o-mini-transcribe", "", ("wav",), OPENAI_MAX_AUDIO_BYTES),
)
PROVIDER_SPECS = {spec.name: spec for spec in PROVIDERS}


@dataclass(frozen=True)
class ProviderConfig:
    """Provider pronto all'uso: abilitato, con la chiave decifrata (mai nel repr)."""
    name: str
    transcribe_model: str
    text_model: str
    api_key: str = field(repr=False)

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDER_SPECS[self.name]
```

- [ ] **Step 3: `settings_store`**

`backend/secondbrain/settings_store.py`:

```python
"""Impostazioni dell'elaborazione e chiavi API cifrate in Postgres (spec §8).

Le chiavi si cifrano con Fernet usando SETTINGS_KEY, che sta solo nel `.env`: un backup
del database da solo non le rivela. Senza SETTINGS_KEY non si salvano chiavi e il
worker resta fermo; tutto il resto del backend funziona.
"""
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from .ai.registry import PROVIDER_SPECS, PROVIDERS, ProviderConfig
from .languages import DEFAULT_LANGUAGE, LANGUAGES
from .models import AiProvider, Setting

PAUSED_KEY = "processing_paused"
LANGUAGE_KEY = "language"
MASK_PREFIX = 4
MASK_SUFFIX = 3
MIN_MASKABLE_LEN = 12  # sotto, anche 7 caratteri su pochi sarebbero troppi da mostrare
MASK_HIDDEN = "…"
MAX_MODEL_LEN = 100  # come le colonne dei modelli
MOVE_UP = "up"
MOVE_DOWN = "down"
NO_KEY = "nessuna chiave"
UNREADABLE_KEY = "chiave illeggibile con la SETTINGS_KEY attuale: reinseriscila"


class NoSettingsKey(RuntimeError):
    """SETTINGS_KEY non impostata: le chiavi API non si possono cifrare."""


class UnknownProvider(LookupError):
    """Nome di provider che il backend non conosce."""


class SecretBox:
    def __init__(self, key: str | None):
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plain: str) -> str:
        if self._fernet is None:
            raise NoSettingsKey("SETTINGS_KEY non impostata")
        return self._fernet.encrypt(plain.encode()).decode("ascii")

    def decrypt(self, token: str) -> str | None:
        """La chiave in chiaro, o None se manca SETTINGS_KEY o il token è di un'altra chiave."""
        if self._fernet is None:
            return None
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            return None


def generate_key() -> str:
    return Fernet.generate_key().decode("ascii")


def mask_key(plain: str) -> str:
    if len(plain) < MIN_MASKABLE_LEN:
        return MASK_HIDDEN
    return f"{plain[:MASK_PREFIX]}{MASK_HIDDEN}{plain[-MASK_SUFFIX:]}"


def get_setting(s: Session, key: str, default: Any) -> Any:
    row = s.get(Setting, key)
    return default if row is None else row.value


def set_setting(s: Session, key: str, value: Any) -> None:
    row = s.get(Setting, key)
    if row is None:
        s.add(Setting(key=key, value=value))
    else:
        row.value = value
    s.flush()


def is_paused(s: Session) -> bool:
    return bool(get_setting(s, PAUSED_KEY, False))


def set_paused(s: Session, paused: bool) -> None:
    set_setting(s, PAUSED_KEY, paused)


def get_language(s: Session) -> str:
    code = get_setting(s, LANGUAGE_KEY, DEFAULT_LANGUAGE)
    return code if code in LANGUAGES else DEFAULT_LANGUAGE


def set_language(s: Session, code: str) -> None:
    if code not in LANGUAGES:
        raise ValueError(f"lingua non supportata: {code!r}")
    set_setting(s, LANGUAGE_KEY, code)


def list_providers(s: Session) -> list[AiProvider]:
    stmt = (select(AiProvider).where(AiProvider.name.in_(PROVIDER_SPECS))
            .order_by(AiProvider.position, AiProvider.name))
    return list(s.scalars(stmt))


def ensure_providers(s: Session, now: datetime) -> list[AiProvider]:
    """Una riga per ogni provider conosciuto, con i default dei modelli; ordine = PROVIDERS."""
    rows = {row.name: row for row in s.scalars(select(AiProvider))}
    position = max((row.position for row in rows.values()), default=0)
    for spec in PROVIDERS:
        if spec.name not in rows:
            position += 1
            s.add(AiProvider(name=spec.name, enabled=True, position=position, api_key_enc=None,
                             transcribe_model=spec.transcribe_model, text_model=spec.text_model,
                             updated_at=now))
    s.flush()
    return list_providers(s)


def _row(s: Session, name: str) -> AiProvider:
    if name not in PROVIDER_SPECS:
        raise UnknownProvider(name)
    row = s.get(AiProvider, name)
    if row is None:
        raise UnknownProvider(name)
    return row


def save_provider(s: Session, box: SecretBox, name: str, *, api_key: str, transcribe_model: str,
                  text_model: str, enabled: bool, now: datetime) -> AiProvider:
    """`api_key` vuota = chiave invariata (il campo nel browser è in sola scrittura)."""
    row = _row(s, name)
    if api_key.strip():
        row.api_key_enc = box.encrypt(api_key.strip())
    row.transcribe_model = transcribe_model.strip()[:MAX_MODEL_LEN]
    row.text_model = text_model.strip()[:MAX_MODEL_LEN]
    row.enabled = enabled
    row.updated_at = now
    s.flush()
    return row


def clear_api_key(s: Session, name: str, now: datetime) -> None:
    row = _row(s, name)
    row.api_key_enc = None
    row.updated_at = now
    s.flush()


def move_provider(s: Session, name: str, direction: str, now: datetime) -> None:
    rows = ensure_providers(s, now)
    names = [row.name for row in rows]
    if name not in names:
        raise UnknownProvider(name)
    index = names.index(name)
    other = index - 1 if direction == MOVE_UP else index + 1
    if not 0 <= other < len(rows):
        return
    rows[index].position, rows[other].position = rows[other].position, rows[index].position
    rows[index].updated_at = rows[other].updated_at = now
    s.flush()


def provider_configs(s: Session, box: SecretBox) -> list[ProviderConfig]:
    """Principale e riserve in ordine, saltando quelli disabilitati o senza chiave leggibile."""
    configs = []
    for row in list_providers(s):
        if not row.enabled or row.api_key_enc is None:
            continue
        key = box.decrypt(row.api_key_enc)
        if key is None:
            continue
        configs.append(ProviderConfig(name=row.name, transcribe_model=row.transcribe_model,
                                      text_model=row.text_model, api_key=key))
    return configs


def has_configured_provider(s: Session) -> bool:
    """Senza decifrare: c'è almeno un provider abilitato con una chiave salvata?"""
    return bool(s.scalar(select(exists().where(AiProvider.enabled.is_(True),
                                               AiProvider.api_key_enc.is_not(None)))))


def key_status(row: AiProvider, box: SecretBox) -> str:
    """Cosa mostrare al posto della chiave: mai la chiave in chiaro."""
    if row.api_key_enc is None:
        return NO_KEY
    plain = box.decrypt(row.api_key_enc)
    return UNREADABLE_KEY if plain is None else mask_key(plain)
```

- [ ] **Step 4: Comando `gen-key`**

In `backend/secondbrain/cli.py`: `from . import catalog, devices, library, ota, settings_store` al posto di `from . import catalog, devices, library, ota`; prima di `COMMAND_GROUPS`:

```python
def _gen_key(args: argparse.Namespace) -> None:
    print(settings_store.generate_key())


def _add_gen_key_command(sub) -> None:
    sub.add_parser("gen-key", help="genera una SETTINGS_KEY per cifrare le chiavi API") \
        .set_defaults(func=_gen_key)
```

e `_add_gen_key_command` in fondo alla tupla `COMMAND_GROUPS`.

- [ ] **Step 5: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/ai/__init__.py backend/secondbrain/ai/registry.py backend/secondbrain/settings_store.py backend/secondbrain/cli.py backend/tests/test_settings_store.py
git commit -m "backend: chiavi API cifrate, impostazioni e default dei provider

Le chiavi dei provider stanno in Postgres cifrate con Fernet e la
SETTINGS_KEY solo nel .env: un dump del database non le rivela e, persa la
SETTINGS_KEY, basta reinserire le chiavi. La UI vedrà solo la versione
mascherata; ProviderConfig non stampa la chiave nemmeno nel repr. Default dei
modelli verificati sulla documentazione il 2026-09-29; gen-key genera la
SETTINGS_KEY."
```

---

### Task 4: Base AI — interfacce, errori, ripulitura, conversione WAV → FLAC

**Files:**
- Create: `backend/secondbrain/ai/audio.py`
- Create: `backend/secondbrain/ai/base.py`
- Test: `backend/tests/test_audio.py`, `backend/tests/test_ai_base.py`

**Interfaces:**
- Consumes: `language_name` (Task 2); `MAX_TITLE_LEN`, `MAX_SUMMARY_LEN`, `MAX_TAGS`, `validate_enrichment`, `EnrichmentInvalid` (Task 2); `PROVIDERS` (Task 3, solo nel test).
- Produces:
  - `ai.audio`: `FLAC = "flac"`, `WAV = "wav"`, `MIME_TYPES`, `AudioError(ValueError)`, `NotConvertible(AudioError)`, `wav_to_flac(src: Path, dst: Path) -> None`, `AudioSource(wav_path: Path, workdir: Path)` con `.wav_path`, `.duration_s: float` (`AudioError` se illeggibile), `.prepare(formats) -> tuple[Path, str]` (FLAC creato una volta sola in `workdir`; campioni non conservabili → prova il formato successivo).
  - `ai.base`: `ProviderError(provider, detail)` con `.provider`, `.detail` e `str()` = `"<provider>: <detail>"`; `ServiceError`, `ContentError`; dataclass `Transcript(text, model, raw)`, `Enrichment(title, summary, tags, model, raw)`, `CheckResult(ok, message)`; protocolli `Transcriber.transcribe(audio: AudioSource, language) -> Transcript`, `Enricher.enrich(text, language) -> Enrichment`, `Provider` (entrambi + `name` + `check() -> CheckResult`); `MAX_ERROR_LEN = 300`; `redact(text, secrets=()) -> str`; `error_message(response)`, `error_code(response)`, `http_detail(response, secrets)`; `send(client, method, url, *, provider, secrets, **kwargs) -> httpx.Response`; `json_body(provider, response) -> dict`; `TRANSCRIBE_TIMEOUT`, `ENRICH_TIMEOUT`, `CHECK_TIMEOUT` (`httpx.Timeout`); `enrich_prompt(language)`, `transcribe_prompt(language)`; `parse_enrichment(provider, model, content, raw) -> Enrichment`; `check_models(available: set[str], wanted) -> CheckResult`.

Nota sull'interfaccia: la spec scrive `transcribe(audio_path, language)`; qui il primo argomento è un `AudioSource`, così il FLAC si crea una volta per nota anche quando la catena prova più provider, e ogni adattatore sceglie il formato con `audio.prepare(self.audio_formats)`.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_audio.py`:

```python
import numpy as np
import pytest
import soundfile as sf

from secondbrain.ai.audio import FLAC, MIME_TYPES, WAV, AudioError, AudioSource, wav_to_flac
from secondbrain.ai.registry import PROVIDERS
from tests.helpers import make_wav

RATE = 16000


def test_flac_keeps_duration_and_every_sample(tmp_path):
    src, dst = tmp_path / "nota.wav", tmp_path / "nota.flac"
    samples = (np.arange(RATE * 2) % 200 - 100).astype("<i2")
    sf.write(str(src), samples, RATE, subtype="PCM_16")
    wav_to_flac(src, dst)
    info = sf.info(str(dst))
    assert (info.format, info.samplerate, info.frames) == ("FLAC", RATE, len(samples))
    back, _ = sf.read(str(dst), dtype="int16")
    assert np.array_equal(back, samples)
    assert dst.stat().st_size < src.stat().st_size


def test_device_wav_becomes_flac_once(tmp_path):
    wav = tmp_path / "091530_dev.wav"
    wav.write_bytes(make_wav(1.0))
    audio = AudioSource(wav, tmp_path)
    path, mime = audio.prepare((FLAC, WAV))
    assert mime == "audio/flac" and path.read_bytes()[:4] == b"fLaC"
    assert audio.prepare((FLAC, WAV)) == (path, mime)
    assert audio.duration_s == pytest.approx(1.0)


def test_wav_only_provider_gets_the_wav(tmp_path):
    wav = tmp_path / "x.wav"
    wav.write_bytes(make_wav(1.0))
    assert AudioSource(wav, tmp_path).prepare((WAV,)) == (wav, "audio/wav")


def test_samples_flac_would_change_fall_back_to_wav(tmp_path):
    wav = tmp_path / "otto_bit.wav"
    sf.write(str(wav), np.zeros(RATE, dtype="int16"), RATE, subtype="PCM_U8")
    audio = AudioSource(wav, tmp_path)
    assert audio.prepare((FLAC, WAV)) == (wav, "audio/wav")
    with pytest.raises(AudioError):
        audio.prepare((FLAC,))


def test_unreadable_audio(tmp_path):
    bad = tmp_path / "rotto.wav"
    bad.write_bytes(b"RIFFxxxxWAVEjunk")
    with pytest.raises(AudioError, match="illeggibile"):
        AudioSource(bad, tmp_path).duration_s
    with pytest.raises(AudioError, match="illeggibile"):
        AudioSource(bad, tmp_path).prepare((FLAC, WAV))


def test_every_provider_format_is_known():
    assert all(fmt in MIME_TYPES for spec in PROVIDERS for fmt in spec.audio_formats)
```

`backend/tests/test_ai_base.py`:

````python
import httpx
import pytest

from secondbrain.ai import base
from secondbrain.ai.base import ServiceError


def test_redact_removes_known_secrets_and_key_shapes():
    text = ("Invalid API Key gsk_abcdef0123456789 on https://x.test/v1?key=AIzaSyA-0123456789"
            "abcdefghijklmnopqrstu and sk-proj-0123456789")
    out = base.redact(text)
    assert "gsk_abcdef" not in out and "AIza" not in out and "sk-proj" not in out
    assert base.redact("la chiave è segreto-noto", ["segreto-noto"]) == "la chiave è ***"


def test_redact_collapses_whitespace_and_limits_length():
    out = base.redact("a\n\n   b " + "x" * 1000)
    assert out.startswith("a b ") and len(out) == base.MAX_ERROR_LEN


def test_error_message_and_code():
    limited = httpx.Response(429, json={"error": {"message": "Rate limit", "code": "rate_limit"}})
    assert (base.error_message(limited), base.error_code(limited)) == ("Rate limit", "rate_limit")
    assert base.error_message(httpx.Response(500, text="boom")) == "boom"
    assert base.error_message(httpx.Response(400, json={"error": "semplice"})) == "semplice"
    assert base.error_code(httpx.Response(400, json={"error": {"code": 400}})) is None


def raising(exc_type):
    def handler(request):
        raise exc_type("simulato", request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_send_turns_timeouts_and_network_errors_into_service_errors():
    with pytest.raises(ServiceError, match="groq: timeout"):
        base.send(raising(httpx.ReadTimeout), "GET", "https://x.test/", provider="groq", secrets=())
    with pytest.raises(ServiceError, match="errore di rete"):
        base.send(raising(httpx.ConnectError), "GET", "https://x.test/", provider="groq",
                  secrets=())


def test_parse_enrichment():
    e = base.parse_enrichment("groq", "m", '```json\n{"title": "T", "summary": "S", '
                              '"tags": ["#A"]}\n```', {"grezza": 1})
    assert (e.title, e.summary, e.tags, e.model, e.raw) == ("T", "S", ("a",), "m", {"grezza": 1})
    with pytest.raises(ServiceError, match="non JSON"):
        base.parse_enrichment("groq", "m", "ecco il titolo", {})
    with pytest.raises(ServiceError, match="fuori schema"):
        base.parse_enrichment("groq", "m", '{"title": "T"}', {})
    with pytest.raises(ServiceError, match="senza testo"):
        base.parse_enrichment("groq", "m", None, {})


def test_prompts_name_the_language_and_the_limits():
    prompt = base.enrich_prompt("it")
    assert "italiano" in prompt and "JSON" in prompt and "200" in prompt and "500" in prompt
    assert "inglese" in base.transcribe_prompt("en")


def test_check_models():
    ok = base.check_models({"a", "b"}, ["a", ""])
    assert ok.ok and "a" in ok.message
    missing = base.check_models({"a"}, ["a", "b"])
    assert not missing.ok and "b" in missing.message


def test_errors_carry_the_provider():
    assert str(ServiceError("groq", "HTTP 429: Rate limit")) == "groq: HTTP 429: Rate limit"
````

Run: `uv run pytest tests/test_audio.py tests/test_ai_base.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'secondbrain.ai.audio'`).

- [ ] **Step 2: `ai/audio.py`**

```python
"""Audio verso i provider (spec §4): il WAV dell'archivio convertito in FLAC senza perdita.

Il FLAC pesa circa la metà (una nota da 10 minuti passa da ~19 MB a ~10 MB) e resta un
file temporaneo; in archivio resta il WAV. Ogni adattatore dice quali formati accetta.
"""
from collections.abc import Sequence
from pathlib import Path

import soundfile as sf

FLAC = "flac"
WAV = "wav"
MIME_TYPES = {FLAC: "audio/flac", WAV: "audio/wav"}
FLAC_SUBTYPES = ("PCM_16", "PCM_24")  # campioni che il FLAC conserva identici
BLOCK_FRAMES = 65536  # conversione a blocchi: mai l'intera nota in memoria


class AudioError(ValueError):
    """Audio illeggibile o in un formato che nessun adattatore accetta."""


class NotConvertible(AudioError):
    """WAV leggibile ma con campioni che il FLAC non conserverebbe identici."""


def wav_to_flac(src: Path, dst: Path) -> None:
    try:
        with sf.SoundFile(str(src)) as wav:
            if wav.subtype not in FLAC_SUBTYPES:
                raise NotConvertible(f"campioni {wav.subtype}: niente FLAC")
            with sf.SoundFile(str(dst), "w", samplerate=wav.samplerate, channels=wav.channels,
                              format="FLAC", subtype=wav.subtype) as flac:
                for block in wav.blocks(blocksize=BLOCK_FRAMES, dtype="int32"):
                    flac.write(block)
    except sf.SoundFileError as exc:
        raise AudioError(f"audio illeggibile: {exc}") from None


class AudioSource:
    """Il WAV di una nota, con il FLAC creato una sola volta in `workdir` se serve."""

    def __init__(self, wav_path: Path, workdir: Path):
        self.wav_path = wav_path
        self._workdir = workdir
        self._flac: Path | None = None
        self._duration: float | None = None

    @property
    def duration_s(self) -> float:
        if self._duration is None:
            try:
                self._duration = float(sf.info(str(self.wav_path)).duration)
            except sf.SoundFileError as exc:
                raise AudioError(f"audio illeggibile: {exc}") from None
        return self._duration

    def _flac_path(self) -> Path:
        if self._flac is None:
            dst = self._workdir / f"{self.wav_path.stem}.{FLAC}"
            wav_to_flac(self.wav_path, dst)
            self._flac = dst
        return self._flac

    def prepare(self, formats: Sequence[str]) -> tuple[Path, str]:
        """(file, tipo MIME) nel primo dei formati accettati che si riesce a produrre."""
        for fmt in formats:
            if fmt == FLAC:
                try:
                    return self._flac_path(), MIME_TYPES[FLAC]
                except NotConvertible:
                    continue
            if fmt == WAV:
                return self.wav_path, MIME_TYPES[WAV]
        raise AudioError(f"nessun formato accettato tra: {', '.join(formats)}")
```

- [ ] **Step 3: `ai/base.py`**

````python
"""Interfacce dei provider, risultati, errori, ripulitura dei messaggi, prompt (spec §4, §5, §8, §9).

Due famiglie di errori decidono cosa fa la catena (spec §5):
- ServiceError: problema del servizio (timeout, rete, 5xx, 429, 401/403, risposta fuori
  schema) → si prova il provider successivo;
- ContentError: problema dell'audio (400/422 sull'audio, oltre il limite) → la nota
  fallisce subito, nessun altro provider.
Ogni messaggio passa da `redact` prima di finire in un'eccezione: niente chiavi, niente
header, niente corpo della richiesta, lunghezza limitata.
"""
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..languages import language_name
from ..notefile import (MAX_SUMMARY_LEN, MAX_TAGS, MAX_TITLE_LEN, EnrichmentInvalid,
                        validate_enrichment)
from .audio import AudioSource

MAX_ERROR_LEN = 300
REDACTED = "***"
KEY_PATTERNS = (
    re.compile(r"gsk_[A-Za-z0-9]{8,}"),                    # Groq
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),                   # OpenAI
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),                 # Google
    re.compile(r"(?i)\b(?:key|api_key|token)=[^&\s\"']+"),  # chiave finita in un URL
)
CONNECT_TIMEOUT_S = 10.0
TRANSCRIBE_TIMEOUT = httpx.Timeout(300.0, connect=CONNECT_TIMEOUT_S)  # 10 min di audio
ENRICH_TIMEOUT = httpx.Timeout(120.0, connect=CONNECT_TIMEOUT_S)
CHECK_TIMEOUT = httpx.Timeout(15.0, connect=CONNECT_TIMEOUT_S)
CODE_FENCE = "```"


class ProviderError(Exception):
    def __init__(self, provider: str, detail: str):
        super().__init__(f"{provider}: {detail}")
        self.provider = provider
        self.detail = detail


class ServiceError(ProviderError):
    """Il servizio non ha risposto come serve: si passa al provider successivo."""


class ContentError(ProviderError):
    """L'audio non va bene per il provider: la nota fallisce subito."""


@dataclass(frozen=True)
class Transcript:
    text: str
    model: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class Enrichment:
    title: str
    summary: str | None
    tags: tuple[str, ...]
    model: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    message: str


class Transcriber(Protocol):
    def transcribe(self, audio: AudioSource, language: str) -> Transcript: ...


class Enricher(Protocol):
    def enrich(self, text: str, language: str) -> Enrichment: ...


class Provider(Transcriber, Enricher, Protocol):
    name: str

    def check(self) -> CheckResult: ...


def redact(text: str, secrets: Iterable[str] = ()) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    for pattern in KEY_PATTERNS:
        text = pattern.sub(REDACTED, text)
    text = " ".join(text.split())
    return text if len(text) <= MAX_ERROR_LEN else text[:MAX_ERROR_LEN - 1] + "…"


def _error_field(response: httpx.Response) -> object:
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("error") if isinstance(body, dict) else None


def error_message(response: httpx.Response) -> str:
    error = _error_field(response)
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"]
    if isinstance(error, str):
        return error
    return response.text


def error_code(response: httpx.Response) -> str | None:
    error = _error_field(response)
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def http_detail(response: httpx.Response, secrets: Iterable[str]) -> str:
    return redact(f"HTTP {response.status_code}: {error_message(response)}", secrets)


def send(client: httpx.Client, method: str, url: str, *, provider: str,
         secrets: Iterable[str], **kwargs: Any) -> httpx.Response:
    """Una richiesta; timeout ed errori di rete diventano ServiceError ripuliti."""
    secrets = tuple(secrets)
    try:
        return client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        raise ServiceError(provider, "timeout") from None
    except httpx.HTTPError as exc:
        raise ServiceError(provider, redact(f"errore di rete ({type(exc).__name__}): {exc}",
                                            secrets)) from None


def json_body(provider: str, response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise ServiceError(provider, "risposta non JSON") from None
    if not isinstance(body, dict):
        raise ServiceError(provider, "risposta JSON inattesa")
    return body


def enrich_prompt(language: str) -> str:
    return (
        "Ricevi la trascrizione di una nota vocale. Rispondi solo con un oggetto JSON con "
        'tre chiavi: "title" (titolo breve su una riga, al massimo '
        f'{MAX_TITLE_LEN} caratteri), "summary" (riassunto di una o due frasi, al massimo '
        f'{MAX_SUMMARY_LEN} caratteri) e "tags" (elenco da 1 a {MAX_TAGS} parole chiave '
        f"minuscole, senza #). Scrivi titolo, riassunto e tag in {language_name(language)}."
    )


def transcribe_prompt(language: str) -> str:
    return (
        f"Trascrivi fedelmente il parlato di questo audio in {language_name(language)}. "
        "Rispondi solo con il testo trascritto, senza commenti, titoli o marcatori di tempo. "
        "Se non c'è parlato rispondi con una stringa vuota."
    )


def _strip_fence(content: str) -> str:
    text = content.strip()
    if text.startswith(CODE_FENCE):
        text = text.removeprefix(CODE_FENCE).removeprefix("json").removesuffix(CODE_FENCE)
    return text.strip()


def parse_enrichment(provider: str, model: str, content: object,
                     raw: dict[str, Any]) -> Enrichment:
    """Testo JSON del modello → Enrichment; fuori schema è un ServiceError (spec §9)."""
    if not isinstance(content, str):
        raise ServiceError(provider, "arricchimento: risposta senza testo")
    try:
        data = json.loads(_strip_fence(content))
    except ValueError:
        raise ServiceError(provider, "arricchimento: risposta non JSON") from None
    try:
        title, summary, tags = validate_enrichment(data)
    except EnrichmentInvalid as exc:
        raise ServiceError(provider, f"arricchimento fuori schema: {exc}") from None
    return Enrichment(title=title, summary=summary, tags=tags, model=model, raw=raw)


def check_models(available: set[str], wanted: Iterable[str]) -> CheckResult:
    """Esito di "Prova": la chiave funziona e i modelli impostati esistono?"""
    wanted = [model for model in wanted if model]
    missing = [model for model in wanted if model not in available]
    if missing:
        return CheckResult(False, f"Chiave valida, ma modelli non trovati: {', '.join(missing)}")
    if not wanted:
        return CheckResult(True, "Chiave valida")
    return CheckResult(True, f"Chiave valida; modelli disponibili: {', '.join(wanted)}")
````

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/ai/audio.py backend/secondbrain/ai/base.py backend/tests/test_audio.py backend/tests/test_ai_base.py
git commit -m "backend: interfacce dei provider AI, errori ripuliti e audio in FLAC

Due famiglie di errori decidono la catena: problemi del servizio fanno
passare al provider successivo, problemi dell'audio fanno fallire la nota
subito. Ogni messaggio che esce da un adattatore è ripulito da chiavi e URL
con la chiave e ha una lunghezza massima. Il WAV va ai provider come FLAC
senza perdita (circa metà peso), creato una volta per nota in un file
temporaneo; verificato che campioni e durata restano identici."
```

---

### Task 5: Adattatore `openai_compatible` (Groq, OpenAI)

**Files:**
- Create: `backend/secondbrain/ai/openai_compatible.py`
- Create: `backend/tests/ai_fakes.py` (transport finto `Recorder`; i Task 7 e 10 lo estendono)
- Test: `backend/tests/test_openai_compatible.py`

**Interfaces:**
- Consumes: `AudioSource`, `AudioError` (Task 4); da `ai.base`: `send`, `json_body`, `http_detail`, `error_code`, `parse_enrichment`, `enrich_prompt`, `check_models`, `ServiceError`, `ContentError`, `Transcript`, `Enrichment`, `CheckResult`, i tre timeout (Task 4); `PROVIDER_SPECS` (Task 3, nei test).
- Produces: `tests.ai_fakes.Recorder(*responses)`: handler per `httpx.MockTransport` che risponde in ordine con le `httpx.Response` date o solleva la classe d'eccezione data (es. `httpx.ReadTimeout`), e tiene le richieste in `.requests`.
- Produces: `OpenAICompatible(name, *, api_key, base_url, transcribe_model, text_model, audio_formats, max_bytes, client: httpx.Client)` che soddisfa `ai.base.Provider`: `transcribe(audio, language) -> Transcript`, `enrich(text, language) -> Enrichment`, `check() -> CheckResult` (non solleva mai).

Richieste (spec §4, §5):
- trascrizione: `POST {base}/audio/transcriptions`, multipart con `file` (FLAC o WAV secondo `audio_formats`), `model`, `language`, `response_format=json` (l'unico accettato anche da `gpt-4o-mini-transcribe`); la durata per l'utilizzo la misura il worker dal WAV, non serve `verbose_json`;
- arricchimento: `POST {base}/chat/completions` con messaggio di sistema (`enrich_prompt`), testo come messaggio utente, `response_format: {"type": "json_object"}`; la risposta si valida con `parse_enrichment`;
- "Prova": `GET {base}/models`, controlla anche che i modelli impostati esistano.

Classificazione: sull'audio `400/413/415/422` → `ContentError`, salvo corpo con `code` `invalid_api_key`, `model_not_found`, `model_decommissioned` (è un problema del servizio); tutto il resto (`401/403/429/5xx`, timeout, rete, risposta fuori schema, qualunque errore sull'arricchimento) → `ServiceError`. Il limite di dimensione si controlla prima di spedire.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/ai_fakes.py`:

```python
"""Provider e transport finti per i test dell'elaborazione AI: nessun test tocca la rete."""
import httpx


class Recorder:
    """Transport finto: risponde in ordine con Response, o solleva la classe d'eccezione data."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, type) and issubclass(item, Exception):
            raise item("simulato", request=request)
        return item
```

`backend/tests/test_openai_compatible.py` (il caso della chiave ripetuta dal provider nel corpo dell'errore è il punto 3 della Review Focus):

```python
import json
import logging

import httpx
import pytest

from secondbrain.ai.audio import AudioSource
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.openai_compatible import OpenAICompatible
from secondbrain.ai.registry import PROVIDER_SPECS
from tests.ai_fakes import Recorder
from tests.helpers import make_wav

KEY = "gsk_test_secret_0123456789abcdef"
TEXT_MODEL_FOR_TESTS = "gpt-test"


def make(name, recorder, **over):
    spec = PROVIDER_SPECS[name]
    fields = dict(api_key=KEY, base_url=spec.base_url, transcribe_model=spec.transcribe_model,
                  text_model=spec.text_model or TEXT_MODEL_FOR_TESTS,
                  audio_formats=spec.audio_formats, max_bytes=spec.max_bytes)
    fields.update(over)
    return OpenAICompatible(name, client=httpx.Client(transport=httpx.MockTransport(recorder)),
                            **fields)


@pytest.fixture
def audio(tmp_path):
    wav = tmp_path / "091530_70041dd8263c.wav"
    wav.write_bytes(make_wav(1.0))
    return AudioSource(wav, tmp_path)


def chat(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def field(name, value):
    return f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()


def test_groq_transcription_request_and_parsing(audio):
    rec = Recorder(httpx.Response(200, json={"text": " Devo chiamare Marco. "}))
    t = make("groq", rec).transcribe(audio, "it")
    req = rec.requests[0]
    assert (req.method, str(req.url)) == ("POST", "https://api.groq.com/openai/v1/audio/transcriptions")
    assert req.headers["authorization"] == f"Bearer {KEY}"
    body = req.content
    assert field("model", "whisper-large-v3-turbo") in body
    assert field("language", "it") in body and field("response_format", "json") in body
    assert b'filename="091530_70041dd8263c.flac"' in body and b"fLaC" in body
    assert (t.text, t.model, t.raw) == ("Devo chiamare Marco.", "whisper-large-v3-turbo",
                                        {"text": " Devo chiamare Marco. "})


def test_openai_gets_the_wav(audio):
    rec = Recorder(httpx.Response(200, json={"text": "ciao"}))
    make("openai", rec).transcribe(audio, "it")
    body = rec.requests[0].content
    assert b'filename="091530_70041dd8263c.wav"' in body and b"WAVE" in body and b"fLaC" not in body
    assert str(rec.requests[0].url) == "https://api.openai.com/v1/audio/transcriptions"


def test_audio_over_the_limit_is_a_content_error_without_calling(audio):
    rec = Recorder()
    with pytest.raises(ContentError, match="oltre il limite"):
        make("groq", rec, max_bytes=10).transcribe(audio, "it")
    assert rec.requests == []


@pytest.mark.parametrize("status,error", [
    (429, ServiceError), (401, ServiceError), (403, ServiceError), (500, ServiceError),
    (503, ServiceError), (400, ContentError), (413, ContentError), (422, ContentError),
])
def test_transcription_errors_are_classified(audio, status, error):
    rec = Recorder(httpx.Response(status, json={"error": {"message": f"errore {status}"}}))
    with pytest.raises(error, match=f"HTTP {status}"):
        make("groq", rec).transcribe(audio, "it")


def test_bad_model_on_the_audio_call_is_a_service_error(audio):
    rec = Recorder(httpx.Response(400, json={"error": {"message": "decommissioned",
                                                       "code": "model_decommissioned"}}))
    with pytest.raises(ServiceError):
        make("groq", rec).transcribe(audio, "it")


def test_timeout_is_a_service_error(audio):
    with pytest.raises(ServiceError, match="timeout"):
        make("groq", Recorder(httpx.ReadTimeout)).transcribe(audio, "it")


def test_the_key_never_leaks(audio, caplog):
    caplog.set_level(logging.DEBUG)
    rec = Recorder(httpx.Response(401, json={"error": {"message": f"Invalid API Key: {KEY}"}}),
                   httpx.Response(401, text=f"bad key {KEY}"))
    provider = make("groq", rec)
    with pytest.raises(ServiceError) as exc:
        provider.transcribe(audio, "it")
    result = provider.check()
    for text in (str(exc.value), exc.value.detail, result.message, caplog.text):
        assert KEY not in text


def test_enrich_request_and_parsing():
    rec = Recorder(chat(json.dumps({"title": "Chiamare Marco", "summary": "Entro venerdì.",
                                    "tags": ["Lavoro", "#casa"]})))
    e = make("groq", rec).enrich("Devo chiamare Marco", "it")
    req = rec.requests[0]
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    payload = json.loads(req.content)
    assert payload["model"] == "openai/gpt-oss-120b"
    assert payload["response_format"] == {"type": "json_object"}
    assert "italiano" in payload["messages"][0]["content"]
    assert payload["messages"][1] == {"role": "user", "content": "Devo chiamare Marco"}
    assert (e.title, e.summary, e.tags, e.model) == ("Chiamare Marco", "Entro venerdì.",
                                                     ("lavoro", "casa"), "openai/gpt-oss-120b")


@pytest.mark.parametrize("response", [
    chat("non è JSON"), chat('{"title": 3}'), httpx.Response(200, json={"choices": []}),
    httpx.Response(400, json={"error": {"message": "bad request"}}),
    httpx.Response(200, text="<html>"),
])
def test_bad_enrichment_goes_to_the_next_provider(response):
    with pytest.raises(ServiceError):
        make("groq", Recorder(response)).enrich("testo", "it")


def test_enrich_without_text_model_does_not_call():
    rec = Recorder()
    with pytest.raises(ServiceError, match="modello di testo"):
        make("openai", rec, text_model="").enrich("testo", "it")
    assert rec.requests == []


def test_check():
    models = {"data": [{"id": "whisper-large-v3-turbo"}, {"id": "openai/gpt-oss-120b"}]}
    rec = Recorder(httpx.Response(200, json=models))
    result = make("groq", rec).check()
    assert result.ok and "whisper-large-v3-turbo" in result.message
    assert (rec.requests[0].method, str(rec.requests[0].url)) == (
        "GET", "https://api.groq.com/openai/v1/models")
    missing = make("groq", Recorder(httpx.Response(200, json={"data": [{"id": "altro"}]}))).check()
    assert not missing.ok and "openai/gpt-oss-120b" in missing.message
    assert not make("groq", Recorder(httpx.ConnectTimeout)).check().ok
```

Run: `uv run pytest tests/test_openai_compatible.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'secondbrain.ai.openai_compatible'`).

- [ ] **Step 2: Implementazione**

`backend/secondbrain/ai/openai_compatible.py`:

```python
"""Adattatore per le API compatibili OpenAI: Groq e OpenAI (spec §4).

Stesse API `/audio/transcriptions` e `/chat/completions`; cambiano URL base, modelli e
formati audio accettati. La chiave va nell'header Authorization.
"""
import httpx

from .audio import AudioError, AudioSource
from .base import (CHECK_TIMEOUT, ENRICH_TIMEOUT, TRANSCRIBE_TIMEOUT, CheckResult, ContentError,
                   Enrichment, ProviderError, ServiceError, Transcript, check_models,
                   enrich_prompt, error_code, http_detail, json_body, parse_enrichment, send)

# Sull'audio questi codici dicono "il file non va bene": nessun altro provider.
AUDIO_CONTENT_STATUSES = (400, 413, 415, 422)
# ...tranne quando il corpo dice che il problema è la chiave o il modello.
SERVICE_ERROR_CODES = ("invalid_api_key", "model_not_found", "model_decommissioned")
OK = 200


class OpenAICompatible:
    def __init__(self, name: str, *, api_key: str, base_url: str, transcribe_model: str,
                 text_model: str, audio_formats: tuple[str, ...], max_bytes: int,
                 client: httpx.Client):
        self.name = name
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.transcribe_model = transcribe_model
        self.text_model = text_model
        self.audio_formats = audio_formats
        self.max_bytes = max_bytes
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"}

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        return send(self._client, method, f"{self.base_url}{path}", provider=self.name,
                    secrets=(self._key,), headers=self._headers(), **kwargs)

    def _error(self, response: httpx.Response, *, audio: bool) -> ProviderError:
        detail = http_detail(response, (self._key,))
        if (audio and response.status_code in AUDIO_CONTENT_STATUSES
                and error_code(response) not in SERVICE_ERROR_CODES):
            return ContentError(self.name, detail)
        return ServiceError(self.name, detail)

    def transcribe(self, audio: AudioSource, language: str) -> Transcript:
        try:
            path, mime = audio.prepare(self.audio_formats)
        except AudioError as exc:
            raise ContentError(self.name, str(exc)) from None
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ContentError(self.name, f"audio di {size} byte oltre il limite di "
                                          f"{self.max_bytes}")
        with open(path, "rb") as f:
            response = self._send(
                "POST", "/audio/transcriptions", timeout=TRANSCRIBE_TIMEOUT,
                data={"model": self.transcribe_model, "language": language,
                      "response_format": "json"},
                files={"file": (path.name, f, mime)})
        if response.status_code != OK:
            raise self._error(response, audio=True)
        body = json_body(self.name, response)
        text = body.get("text")
        if not isinstance(text, str):
            raise ServiceError(self.name, "trascrizione: risposta senza testo")
        return Transcript(text=text.strip(), model=self.transcribe_model, raw=body)

    def enrich(self, text: str, language: str) -> Enrichment:
        if not self.text_model:
            raise ServiceError(self.name, "modello di testo non impostato")
        payload = {
            "model": self.text_model,
            "messages": [{"role": "system", "content": enrich_prompt(language)},
                         {"role": "user", "content": text}],
            "response_format": {"type": "json_object"},
        }
        response = self._send("POST", "/chat/completions", timeout=ENRICH_TIMEOUT, json=payload)
        if response.status_code != OK:
            raise self._error(response, audio=False)
        body = json_body(self.name, response)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise ServiceError(self.name, "arricchimento: risposta senza contenuto") from None
        return parse_enrichment(self.name, self.text_model, content, body)

    def check(self) -> CheckResult:
        """"Prova": elenco dei modelli, chiamata gratuita che verifica anche la chiave."""
        try:
            response = self._send("GET", "/models", timeout=CHECK_TIMEOUT)
            if response.status_code != OK:
                return CheckResult(False, self._error(response, audio=False).detail)
            body = json_body(self.name, response)
        except ServiceError as exc:
            return CheckResult(False, exc.detail)
        data = body.get("data")
        models = data if isinstance(data, list) else []
        ids = {str(m.get("id")) for m in models if isinstance(m, dict)}
        return check_models(ids, (self.transcribe_model, self.text_model))
```

- [ ] **Step 3: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/secondbrain/ai/openai_compatible.py backend/tests/ai_fakes.py backend/tests/test_openai_compatible.py
git commit -m "backend: adattatore OpenAI compatibile per Groq e OpenAI

Stesse API di trascrizione e chat per entrambi, cambiano URL, modelli e
formati: Groq riceve il FLAC, OpenAI il WAV finché il FLAC non è confermato.
Gli errori sull'audio fanno fallire la nota, quelli del servizio (chiave,
quota, 5xx, JSON fuori schema) fanno passare alla riserva. Provato solo con
MockTransport, compreso il caso di un provider che ripete la chiave nel
messaggio d'errore."
```

---

### Task 6: Adattatore `gemini` e scelta dell'adattatore

**Files:**
- Create: `backend/secondbrain/ai/gemini.py`
- Modify: `backend/secondbrain/ai/registry.py` (import e `build_provider`)
- Test: `backend/tests/test_gemini.py`

**Interfaces:**
- Consumes: come il Task 5 (`ai.base`, `AudioSource`), più `error_message` e `transcribe_prompt`; `ProviderConfig`, `PROVIDER_SPECS`, `KIND_GEMINI` (Task 3); `OpenAICompatible` (Task 5); `tests.ai_fakes.Recorder` (Task 5).
- Produces: `Gemini(name, *, api_key, base_url, transcribe_model, text_model, audio_formats, max_bytes, client)` con la stessa interfaccia di `OpenAICompatible` (`max_bytes` qui è il limite della richiesta intera); `ai.registry.build_provider(config: ProviderConfig, client: httpx.Client) -> Provider`.

Richieste (spec §4):
- trascrizione: `POST {base}/models/{model}:generateContent`, header `x-goog-api-key`, parti `[prompt di trascrizione nella lingua, inline_data {mime_type, data base64}]`; la richiesta intera deve stare sotto i 20 MB (si misura il corpo JSON prima di spedire); testo = parti della prima candidata, escluse quelle `thought`;
- arricchimento: stessa chiamata con `systemInstruction` (`enrich_prompt`), testo come contenuto utente, `generationConfig.responseMimeType = "application/json"` e `responseSchema` con i tre campi obbligatori; la temperatura resta quella di default (per i Gemini 3 la documentazione sconsiglia di cambiarla);
- "Prova": `GET {base}/models?pageSize=1000`, nomi senza il prefisso `models/`.

Classificazione: `400/413` sull'audio → `ContentError`, **salvo** chiave sbagliata o scaduta (`details[].reason` `API_KEY_INVALID`/`API_KEY_EXPIRED` o messaggio che parla di "API key"): Gemini la segnala con `400 INVALID_ARGUMENT` e senza questa eccezione una chiave revocata farebbe fallire tutte le note invece di passare alla riserva (Review Focus 2). Risposta bloccata (`promptFeedback.blockReason` o `finishReason` di sicurezza) → `ContentError` sull'audio, `ServiceError` sull'arricchimento.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_gemini.py`:

```python
import base64
import json
import logging

import httpx
import pytest

from secondbrain.ai.audio import AudioSource
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.gemini import Gemini
from secondbrain.ai.openai_compatible import OpenAICompatible
from secondbrain.ai.registry import PROVIDER_SPECS, ProviderConfig, build_provider
from tests.ai_fakes import Recorder
from tests.helpers import make_wav

KEY = "AIza-test-gemini-0123456789abcdefghij"
GENERATE_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
                "gemini-3.8-flash:generateContent")
BAD_KEY = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                     "status": "INVALID_ARGUMENT",
                     "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo",
                                  "reason": "API_KEY_INVALID", "domain": "googleapis.com"}]}}


def make(recorder, **over):
    spec = PROVIDER_SPECS["gemini"]
    fields = dict(api_key=KEY, base_url=spec.base_url, transcribe_model=spec.transcribe_model,
                  text_model=spec.text_model, audio_formats=spec.audio_formats,
                  max_bytes=spec.max_bytes)
    fields.update(over)
    return Gemini("gemini", client=httpx.Client(transport=httpx.MockTransport(recorder)), **fields)


def answer(*texts, finish="STOP"):
    parts = [{"text": t} for t in texts]
    return httpx.Response(200, json={"candidates": [{"content": {"parts": parts, "role": "model"},
                                                     "finishReason": finish}]})


@pytest.fixture
def audio(tmp_path):
    wav = tmp_path / "091530_70041dd8263c.wav"
    wav.write_bytes(make_wav(1.0))
    return AudioSource(wav, tmp_path)


def test_transcription_request_and_parsing(audio):
    thought = {"candidates": [{"content": {"parts": [
        {"text": "sto pensando", "thought": True}, {"text": " Devo chiamare "},
        {"text": "Marco. "}]}, "finishReason": "STOP"}]}
    rec = Recorder(httpx.Response(200, json=thought))
    t = make(rec).transcribe(audio, "it")
    req = rec.requests[0]
    assert (req.method, str(req.url)) == ("POST", GENERATE_URL)
    assert req.headers["x-goog-api-key"] == KEY
    assert KEY not in str(req.url) and "key=" not in str(req.url)
    parts = json.loads(req.content)["contents"][0]["parts"]
    assert "italiano" in parts[0]["text"]
    assert parts[1]["inline_data"]["mime_type"] == "audio/flac"
    assert base64.b64decode(parts[1]["inline_data"]["data"])[:4] == b"fLaC"
    assert (t.text, t.model) == ("Devo chiamare Marco.", "gemini-3.8-flash")


def test_request_over_the_inline_limit_is_a_content_error(audio):
    rec = Recorder()
    with pytest.raises(ContentError, match="oltre il limite"):
        make(rec, max_bytes=100).transcribe(audio, "it")
    assert rec.requests == []


def test_wrong_key_is_a_service_error_even_with_400(audio):
    with pytest.raises(ServiceError, match="HTTP 400"):
        make(Recorder(httpx.Response(400, json=BAD_KEY))).transcribe(audio, "it")


@pytest.mark.parametrize("status,error", [
    (400, ContentError), (413, ContentError), (429, ServiceError), (403, ServiceError),
    (500, ServiceError), (503, ServiceError),
])
def test_transcription_errors_are_classified(audio, status, error):
    body = {"error": {"code": status, "message": "Request contains an invalid argument.",
                      "status": "INVALID_ARGUMENT"}}
    with pytest.raises(error):
        make(Recorder(httpx.Response(status, json=body))).transcribe(audio, "it")


def test_blocked_audio_is_a_content_error(audio):
    blocked = httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})
    with pytest.raises(ContentError, match="SAFETY"):
        make(Recorder(blocked)).transcribe(audio, "it")
    with pytest.raises(ContentError, match="RECITATION"):
        make(Recorder(answer("x", finish="RECITATION"))).transcribe(audio, "it")


def test_enrich_request_and_parsing():
    rec = Recorder(answer(json.dumps({"title": "Chiamare Marco", "summary": "Entro venerdì.",
                                      "tags": ["Lavoro"]})))
    e = make(rec).enrich("Devo chiamare Marco", "it")
    payload = json.loads(rec.requests[0].content)
    assert "italiano" in payload["systemInstruction"]["parts"][0]["text"]
    assert payload["contents"][0]["parts"][0]["text"] == "Devo chiamare Marco"
    config = payload["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["title", "summary", "tags"]
    assert (e.title, e.summary, e.tags, e.model) == ("Chiamare Marco", "Entro venerdì.",
                                                     ("lavoro",), "gemini-3.8-flash")


@pytest.mark.parametrize("response", [
    answer("non JSON"), answer('{"title": "T"}'),
    httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
    httpx.Response(400, json={"error": {"message": "invalid"}}),
])
def test_bad_enrichment_goes_to_the_next_provider(response):
    with pytest.raises(ServiceError):
        make(Recorder(response)).enrich("testo", "it")


def test_check_lists_models_with_the_key_in_the_header():
    rec = Recorder(httpx.Response(200, json={"models": [{"name": "models/gemini-3.8-flash"}]}))
    result = make(rec).check()
    req = rec.requests[0]
    assert result.ok and req.method == "GET"
    assert req.url.path == "/v1beta/models" and req.url.params["pageSize"] == "1000"
    assert req.headers["x-goog-api-key"] == KEY and KEY not in str(req.url)
    assert not make(Recorder(httpx.Response(200, json={"models": []}))).check().ok


def test_the_key_never_leaks(audio, caplog):
    caplog.set_level(logging.DEBUG)
    echo = {"error": {"code": 403, "message": f"key {KEY} blocked", "status": "PERMISSION_DENIED"}}
    rec = Recorder(httpx.Response(403, json=echo), httpx.Response(400, json=BAD_KEY))
    provider = make(rec)
    with pytest.raises(ServiceError) as exc:
        provider.transcribe(audio, "it")
    result = provider.check()
    for text in (str(exc.value), result.message, caplog.text):
        assert KEY not in text


def test_build_provider_picks_the_adapter():
    client = httpx.Client(transport=httpx.MockTransport(Recorder()))
    gemini = build_provider(ProviderConfig("gemini", "gemini-3.8-flash", "gemini-3.8-flash", KEY),
                            client)
    groq = build_provider(ProviderConfig("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b",
                                         "gsk_test_0123456789"), client)
    assert isinstance(gemini, Gemini) and gemini.base_url == PROVIDER_SPECS["gemini"].base_url
    assert isinstance(groq, OpenAICompatible) and groq.audio_formats == ("flac", "wav")
    assert groq.transcribe_model == "whisper-large-v3-turbo"
```

Run: `uv run pytest tests/test_gemini.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'secondbrain.ai.gemini'`).

- [ ] **Step 2: `ai/gemini.py`**

```python
"""Adattatore Gemini (spec §4): audio inline a `generateContent`, poi una seconda chiamata
di solo testo per l'arricchimento. La chiave va nell'header `x-goog-api-key`, mai nell'URL.
"""
import base64
import json

import httpx

from .audio import AudioError, AudioSource
from .base import (CHECK_TIMEOUT, ENRICH_TIMEOUT, TRANSCRIBE_TIMEOUT, CheckResult, ContentError,
                   Enrichment, ProviderError, ServiceError, Transcript, check_models,
                   enrich_prompt, error_message, http_detail, json_body, parse_enrichment, send,
                   transcribe_prompt)

OK = 200
AUDIO_CONTENT_STATUSES = (400, 413)
# Gemini risponde 400 INVALID_ARGUMENT anche a una chiave sbagliata: non è colpa
# dell'audio, si passa alla riserva (Review Focus 2).
KEY_ERROR_REASONS = ("API_KEY_INVALID", "API_KEY_EXPIRED")
KEY_ERROR_HINT = "api key"
BLOCKED_FINISH_REASONS = ("SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII")
LIST_PAGE_SIZE = 1000
MODEL_PREFIX = "models/"
ENRICH_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "title": {"type": "STRING"},
        "summary": {"type": "STRING"},
        "tags": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["title", "summary", "tags"],
}


def _reasons(response: httpx.Response) -> set[str]:
    try:
        error = response.json().get("error") or {}
        details = error.get("details") or []
        return {d.get("reason") for d in details if isinstance(d, dict)} - {None}
    except (ValueError, AttributeError):
        return set()


class Gemini:
    def __init__(self, name: str, *, api_key: str, base_url: str, transcribe_model: str,
                 text_model: str, audio_formats: tuple[str, ...], max_bytes: int,
                 client: httpx.Client):
        self.name = name
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.transcribe_model = transcribe_model
        self.text_model = text_model
        self.audio_formats = audio_formats
        self.max_bytes = max_bytes  # richiesta intera, audio in base64 compreso
        self._client = client

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._key}

    def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = self._headers() | kwargs.pop("headers", {})
        return send(self._client, method, f"{self.base_url}{path}", provider=self.name,
                    secrets=(self._key,), headers=headers, **kwargs)

    def _error(self, response: httpx.Response, *, audio: bool) -> ProviderError:
        detail = http_detail(response, (self._key,))
        key_problem = (bool(_reasons(response) & set(KEY_ERROR_REASONS))
                       or KEY_ERROR_HINT in error_message(response).lower())
        if audio and response.status_code in AUDIO_CONTENT_STATUSES and not key_problem:
            return ContentError(self.name, detail)
        return ServiceError(self.name, detail)

    def _generate(self, model: str, payload: dict, timeout: httpx.Timeout, *,
                  audio: bool) -> dict:
        body = json.dumps(payload).encode()
        if len(body) > self.max_bytes:
            raise ContentError(self.name, f"richiesta di {len(body)} byte oltre il limite di "
                                          f"{self.max_bytes}")
        response = self._send("POST", f"/models/{model}:generateContent", content=body,
                              headers={"Content-Type": "application/json"}, timeout=timeout)
        if response.status_code != OK:
            raise self._error(response, audio=audio)
        return json_body(self.name, response)

    def _text(self, body: dict, *, audio: bool) -> str:
        blocked = ContentError if audio else ServiceError
        candidates = body.get("candidates") or []
        if not candidates:
            reason = (body.get("promptFeedback") or {}).get("blockReason", "nessuna risposta")
            raise blocked(self.name, f"risposta bloccata: {reason}")
        first = candidates[0]
        finish = first.get("finishReason")
        if finish in BLOCKED_FINISH_REASONS:
            raise blocked(self.name, f"risposta interrotta: {finish}")
        parts = (first.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts
                       if isinstance(p, dict) and not p.get("thought"))

    def transcribe(self, audio: AudioSource, language: str) -> Transcript:
        try:
            path, mime = audio.prepare(self.audio_formats)
        except AudioError as exc:
            raise ContentError(self.name, str(exc)) from None
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        payload = {"contents": [{"role": "user", "parts": [
            {"text": transcribe_prompt(language)},
            {"inline_data": {"mime_type": mime, "data": data}},
        ]}]}
        body = self._generate(self.transcribe_model, payload, TRANSCRIBE_TIMEOUT, audio=True)
        return Transcript(text=self._text(body, audio=True).strip(), model=self.transcribe_model,
                          raw=body)

    def enrich(self, text: str, language: str) -> Enrichment:
        if not self.text_model:
            raise ServiceError(self.name, "modello di testo non impostato")
        payload = {
            "systemInstruction": {"parts": [{"text": enrich_prompt(language)}]},
            "contents": [{"role": "user", "parts": [{"text": text}]}],
            "generationConfig": {"responseMimeType": "application/json",
                                 "responseSchema": ENRICH_SCHEMA},
        }
        body = self._generate(self.text_model, payload, ENRICH_TIMEOUT, audio=False)
        return parse_enrichment(self.name, self.text_model, self._text(body, audio=False), body)

    def check(self) -> CheckResult:
        try:
            response = self._send("GET", "/models", params={"pageSize": LIST_PAGE_SIZE},
                                  timeout=CHECK_TIMEOUT)
            if response.status_code != OK:
                return CheckResult(False, self._error(response, audio=False).detail)
            body = json_body(self.name, response)
        except ServiceError as exc:
            return CheckResult(False, exc.detail)
        models = body.get("models")
        names = {str(m.get("name", "")).removeprefix(MODEL_PREFIX)
                 for m in (models if isinstance(models, list) else []) if isinstance(m, dict)}
        return check_models(names, (self.transcribe_model, self.text_model))
```

- [ ] **Step 3: `build_provider` nel registro**

`backend/secondbrain/ai/registry.py` (file completo: rispetto al Task 3 cambiano gli import e c'è `build_provider` in fondo):

```python
"""Provider conosciuti con i default dei modelli (spec §4) e la configurazione in uso.

Default verificati sulla documentazione dei provider il 2026-09-29; si cambiano dalla
pagina impostazioni e si controllano col pulsante "Prova".
"""
from dataclasses import dataclass, field

import httpx

from .base import Provider
from .gemini import Gemini
from .openai_compatible import OpenAICompatible

KIND_OPENAI_COMPATIBLE = "openai_compatible"
KIND_GEMINI = "gemini"
MB = 1000 * 1000  # i provider dichiarano i limiti in MB decimali: così si resta sotto
GROQ_MAX_AUDIO_BYTES = 25 * MB
OPENAI_MAX_AUDIO_BYTES = 25 * MB
GEMINI_MAX_REQUEST_BYTES = 20 * MB  # richiesta inline intera, audio in base64 compreso


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    label: str
    kind: str
    base_url: str
    transcribe_model: str
    text_model: str
    audio_formats: tuple[str, ...]  # formati di ai.audio, in ordine di preferenza
    max_bytes: int                  # file audio (OpenAI compatibile) o richiesta intera (Gemini)


PROVIDERS = (
    ProviderSpec("groq", "Groq", KIND_OPENAI_COMPATIBLE, "https://api.groq.com/openai/v1",
                 "whisper-large-v3-turbo", "openai/gpt-oss-120b", ("flac", "wav"),
                 GROQ_MAX_AUDIO_BYTES),
    ProviderSpec("gemini", "Gemini", KIND_GEMINI,
                 "https://generativelanguage.googleapis.com/v1beta",
                 "gemini-3.8-flash", "gemini-3.8-flash", ("flac", "wav"),
                 GEMINI_MAX_REQUEST_BYTES),
    # FLAC non confermato per OpenAI: si manda il WAV. Modello di testo da scegliere.
    ProviderSpec("openai", "OpenAI", KIND_OPENAI_COMPATIBLE, "https://api.openai.com/v1",
                 "gpt-4o-mini-transcribe", "", ("wav",), OPENAI_MAX_AUDIO_BYTES),
)
PROVIDER_SPECS = {spec.name: spec for spec in PROVIDERS}


@dataclass(frozen=True)
class ProviderConfig:
    """Provider pronto all'uso: abilitato, con la chiave decifrata (mai nel repr)."""
    name: str
    transcribe_model: str
    text_model: str
    api_key: str = field(repr=False)

    @property
    def spec(self) -> ProviderSpec:
        return PROVIDER_SPECS[self.name]


def build_provider(config: ProviderConfig, client: httpx.Client) -> Provider:
    """L'adattatore giusto per il provider, con i modelli scelti nelle impostazioni."""
    spec = config.spec
    adapter = Gemini if spec.kind == KIND_GEMINI else OpenAICompatible
    return adapter(config.name, api_key=config.api_key, base_url=spec.base_url,
                   transcribe_model=config.transcribe_model, text_model=config.text_model,
                   audio_formats=spec.audio_formats, max_bytes=spec.max_bytes, client=client)
```

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/ai/gemini.py backend/secondbrain/ai/registry.py backend/tests/test_gemini.py
git commit -m "backend: adattatore Gemini con la chiave nell'header

Audio inline a generateContent e seconda chiamata di solo testo con uno
schema JSON fisso. La chiave va in x-goog-api-key e mai nell'URL, così non
finisce nei log delle richieste. Gemini segnala una chiave sbagliata con 400
INVALID_ARGUMENT: la si riconosce dal motivo API_KEY_INVALID e si passa alla
riserva invece di far fallire la nota come se l'audio fosse sbagliato."
```

---

### Task 7: Catena dei provider con fallback e utilizzo mensile

**Files:**
- Create: `backend/secondbrain/ai/chain.py`
- Create: `backend/secondbrain/usage.py`
- Modify: `backend/tests/ai_fakes.py` (provider finti)
- Test: `backend/tests/test_chain.py`

**Interfaces:**
- Consumes: `ServiceError`, `ContentError`, `Transcript`, `Enrichment`, `CheckResult`, `Provider` (Task 4); `AudioSource` (Task 4); `ProviderConfig` (Task 3); `AiUsage` (Task 1).
- Produces:
  - `ai.chain`: `ProviderFactory = Callable[[ProviderConfig], Provider]`, `UsageCallback = Callable[[str, float], None]`, `AllProvidersFailed(errors)` con `.errors` e `str()` = errori separati da `; `; `transcribe(configs, factory, audio, language, on_call) -> tuple[ProviderConfig, Transcript]`, `enrich(configs, factory, text, language, on_call) -> tuple[ProviderConfig, Enrichment]`. `on_call(nome, secondi)` a ogni chiamata fatta: secondi di audio solo per una trascrizione riuscita, 0 altrimenti. Un provider senza modello per la fase si salta.
  - `usage`: `month_start(now, tz) -> date`, `record_usage(s, provider, month, audio_seconds, calls=1)` (upsert che somma, niente commit), `usage_for_month(s, month) -> dict[str, AiUsage]`.
  - `tests.ai_fakes`: `DEFAULT_TEXT`, `DEFAULT_ENRICHMENT`, `FakeProvider(name, transcripts=(), enrichments=())` con `.calls` (`("transcribe", lingua)` / `("enrich", testo)`) e code di risposte (testo o tupla, eccezione, oppure funzione chiamata durante la chiamata), `FakeFactory(**providers)`.

La catena riceve solo provider già utilizzabili (abilitati e con chiave leggibile: `settings_store.provider_configs`, Task 3); qui si provano ordine, fallback e contabilità.

- [ ] **Step 1: Provider finti e test (falliscono)**

`backend/tests/ai_fakes.py` (file completo):

```python
"""Provider e transport finti per i test dell'elaborazione AI: nessun test tocca la rete."""
import httpx

from secondbrain.ai.base import CheckResult, Enrichment, Transcript


class Recorder:
    """Transport finto: risponde in ordine con Response, o solleva la classe d'eccezione data."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        request.read()
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, type) and issubclass(item, Exception):
            raise item("simulato", request=request)
        return item


DEFAULT_TEXT = "Devo chiamare Marco per il preventivo del tetto."
DEFAULT_ENRICHMENT = ("Chiamare Marco", "Chiamare Marco per il preventivo.", ("lavoro", "casa"))


def _next(queue: list, default):
    item = queue.pop(0) if queue else default
    if callable(item):  # azione da fare "durante" la chiamata (es. l'utente che corregge)
        item = item()
    if isinstance(item, BaseException):
        raise item
    return item


class FakeProvider:
    """Provider finto: risposte in coda (testo, tupla, eccezione o funzione), poi i default."""

    def __init__(self, name: str, transcripts=(), enrichments=()):
        self.name = name
        self.transcripts = list(transcripts)
        self.enrichments = list(enrichments)
        self.calls: list[tuple[str, str]] = []

    def transcribe(self, audio, language):
        self.calls.append(("transcribe", language))
        text = _next(self.transcripts, DEFAULT_TEXT)
        return Transcript(text=text, model=f"{self.name}-stt", raw={"text": text})

    def enrich(self, text, language):
        self.calls.append(("enrich", text))
        title, summary, tags = _next(self.enrichments, DEFAULT_ENRICHMENT)
        return Enrichment(title=title, summary=summary, tags=tuple(tags),
                          model=f"{self.name}-llm", raw={"title": title})

    def check(self):
        return CheckResult(True, "ok")


class FakeFactory:
    """Al posto di build_provider: restituisce il provider finto con quel nome."""

    def __init__(self, **providers: FakeProvider):
        self.providers = providers

    def __call__(self, config):
        return self.providers[config.name]
```

`backend/tests/test_chain.py`:

```python
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain.ai import chain
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.ai.registry import ProviderConfig
from secondbrain.usage import month_start, record_usage, usage_for_month
from tests.ai_fakes import FakeFactory, FakeProvider

GROQ = ProviderConfig("groq", "whisper-large-v3-turbo", "openai/gpt-oss-120b", "gsk_test_1")
GEMINI = ProviderConfig("gemini", "gemini-3.8-flash", "gemini-3.8-flash", "AIza-test-2")
OPENAI = ProviderConfig("openai", "gpt-4o-mini-transcribe", "", "sk-test-3")


class StubAudio:
    duration_s = 12.5


@pytest.fixture
def calls():
    return []


def on_call(calls):
    return lambda name, seconds: calls.append((name, seconds))


def test_primary_answers_and_the_reserve_is_not_called(calls):
    groq, gemini = FakeProvider("groq"), FakeProvider("gemini")
    config, t = chain.transcribe([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini),
                                 StubAudio(), "it", on_call(calls))
    assert (config.name, t.model) == ("groq", "groq-stt")
    assert gemini.calls == [] and calls == [("groq", 12.5)]


def test_rate_limited_primary_falls_back_to_the_reserve(calls):
    groq = FakeProvider("groq", transcripts=[ServiceError("groq", "HTTP 429: Rate limit")])
    gemini = FakeProvider("gemini")
    config, _ = chain.transcribe([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini),
                                 StubAudio(), "it", on_call(calls))
    assert config.name == "gemini" and calls == [("groq", 0.0), ("gemini", 12.5)]


def test_content_error_stops_the_chain(calls):
    groq = FakeProvider("groq", transcripts=[ContentError("groq", "HTTP 400: audio non valido")])
    gemini = FakeProvider("gemini")
    with pytest.raises(ContentError):
        chain.transcribe([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini), StubAudio(), "it",
                         on_call(calls))
    assert gemini.calls == [] and calls == [("groq", 0.0)]


def test_all_failing_reports_every_error(calls):
    groq = FakeProvider("groq", enrichments=[ServiceError("groq", "HTTP 503: giù")])
    gemini = FakeProvider("gemini", enrichments=[ServiceError("gemini", "timeout")])
    with pytest.raises(chain.AllProvidersFailed) as exc:
        chain.enrich([GROQ, GEMINI], FakeFactory(groq=groq, gemini=gemini), "testo", "it",
                     on_call(calls))
    assert str(exc.value) == "groq: HTTP 503: giù; gemini: timeout"
    assert len(exc.value.errors) == 2


def test_provider_without_a_model_for_the_stage_is_skipped(calls):
    openai, gemini = FakeProvider("openai"), FakeProvider("gemini")
    config, _ = chain.enrich([OPENAI, GEMINI], FakeFactory(openai=openai, gemini=gemini), "testo",
                             "it", on_call(calls))
    assert config.name == "gemini" and openai.calls == []
    config, _ = chain.transcribe([OPENAI, GEMINI], FakeFactory(openai=openai, gemini=gemini),
                                 StubAudio(), "it", on_call(calls))
    assert config.name == "openai"


def test_no_usable_provider(calls):
    with pytest.raises(chain.AllProvidersFailed, match="nessun provider"):
        chain.enrich([], FakeFactory(), "testo", "it", on_call(calls))


def test_usage_accumulates_per_provider_and_month(db):
    september, october = date(2026, 9, 1), date(2026, 10, 1)
    record_usage(db, "groq", september, 60.0)
    record_usage(db, "groq", september, 30.0)
    record_usage(db, "groq", october, 5.0)
    record_usage(db, "gemini", september, 0.0)
    db.commit()
    usage = usage_for_month(db, september)
    assert (usage["groq"].audio_seconds, usage["groq"].calls) == (90.0, 2)
    assert (usage["gemini"].audio_seconds, usage["gemini"].calls) == (0.0, 1)
    assert usage_for_month(db, october)["groq"].calls == 1


def test_month_is_the_local_one():
    rome = ZoneInfo("Europe/Rome")
    assert month_start(datetime.fromisoformat("2026-09-30T22:30:00+00:00"), rome) == date(2026, 10, 1)
```

Run: `uv run pytest tests/test_chain.py -q`
Expected: FAIL (`ImportError: cannot import name 'chain'`).

- [ ] **Step 2: `ai/chain.py`**

```python
"""Catena dei provider (spec §5): il principale, poi le riserve nell'ordine delle impostazioni.

Ogni fase percorre la catena da capo: chi ha trascritto non vincola chi arricchisce.
Un ServiceError fa passare al successivo; un ContentError ferma tutto (la nota fallisce).
"""
import logging
from collections.abc import Callable, Sequence
from typing import TypeVar

from .audio import AudioSource
from .base import ContentError, Enrichment, Provider, ServiceError, Transcript
from .registry import ProviderConfig

log = logging.getLogger(__name__)

ProviderFactory = Callable[[ProviderConfig], Provider]
UsageCallback = Callable[[str, float], None]  # (provider, secondi di audio trascritti)
T = TypeVar("T")


class AllProvidersFailed(Exception):
    """Nessun provider ha risposto: il lavoro torna in coda con backoff."""

    def __init__(self, errors: Sequence[ServiceError]):
        self.errors = list(errors)
        super().__init__("; ".join(str(e) for e in self.errors)
                         or "nessun provider utilizzabile per questa fase")


def _run(configs: Sequence[ProviderConfig], factory: ProviderFactory,
         has_model: Callable[[ProviderConfig], str], call: Callable[[Provider], T],
         on_call: UsageCallback, audio_seconds: float) -> tuple[ProviderConfig, T]:
    errors: list[ServiceError] = []
    for config in configs:
        if not has_model(config):
            continue
        provider = factory(config)
        try:
            result = call(provider)
        except ServiceError as exc:
            on_call(config.name, 0.0)
            log.warning("%s non disponibile, provo il successivo: %s", config.name, exc.detail)
            errors.append(exc)
            continue
        except ContentError:
            on_call(config.name, 0.0)
            raise
        on_call(config.name, audio_seconds)
        return config, result
    raise AllProvidersFailed(errors)


def transcribe(configs: Sequence[ProviderConfig], factory: ProviderFactory, audio: AudioSource,
               language: str, on_call: UsageCallback) -> tuple[ProviderConfig, Transcript]:
    seconds = audio.duration_s  # AudioError qui, prima di disturbare qualunque provider
    return _run(configs, factory, lambda c: c.transcribe_model,
                lambda p: p.transcribe(audio, language), on_call, seconds)


def enrich(configs: Sequence[ProviderConfig], factory: ProviderFactory, text: str,
           language: str, on_call: UsageCallback) -> tuple[ProviderConfig, Enrichment]:
    return _run(configs, factory, lambda c: c.text_model,
                lambda p: p.enrich(text, language), on_call, 0.0)
```

- [ ] **Step 3: `usage.py`**

```python
"""Utilizzo dei provider per mese: secondi di audio trascritti e chiamate (spec §7, §8)."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from .models import AiUsage


def month_start(now: datetime, tz: ZoneInfo) -> date:
    """Primo giorno del mese nel fuso dell'archivio: il mese è quello che vede l'utente."""
    return now.astimezone(tz).date().replace(day=1)


def record_usage(s: Session, provider: str, month: date, audio_seconds: float,
                 calls: int = 1) -> None:
    stmt = pg_insert(AiUsage).values(provider=provider, month=month,
                                     audio_seconds=audio_seconds, calls=calls)
    stmt = stmt.on_conflict_do_update(
        index_elements=[AiUsage.provider, AiUsage.month],
        set_={"audio_seconds": AiUsage.audio_seconds + stmt.excluded.audio_seconds,
              "calls": AiUsage.calls + stmt.excluded.calls})
    s.execute(stmt)


def usage_for_month(s: Session, month: date) -> dict[str, AiUsage]:
    return {row.provider: row for row in s.scalars(select(AiUsage).where(AiUsage.month == month))}
```

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/ai/chain.py backend/secondbrain/usage.py backend/tests/ai_fakes.py backend/tests/test_chain.py
git commit -m "backend: catena principale-riserve e utilizzo mensile dei provider

Una sola elaborazione per nota: si prova il principale e poi le riserve in
ordine, passando oltre sugli errori del servizio e fermandosi su quelli
dell'audio. Ogni fase riparte dall'inizio della catena. Le chiamate e i
secondi di audio si contano per provider e mese, per la pagina impostazioni."
```

---

### Task 8: Coda — `jobs`, lavoro creato all'ingest, cestino, Rielabora, `secondbrain process`

**Files:**
- Create: `backend/secondbrain/jobs.py`
- Modify: `backend/secondbrain/ingest.py` (`_store`)
- Modify: `backend/secondbrain/library.py` (`trash_capture`, nuova `reprocess`)
- Modify: `backend/secondbrain/cli.py` (comando `process`)
- Test: `backend/tests/test_jobs.py`

**Interfaces:**
- Consumes: `Job`, `Capture` (Task 1); `library._get`, `InTrash`, `NotFound` (esistenti).
- Produces:
  - `jobs`: stati `QUEUED`, `RUNNING`, `DONE`, `FAILED`, `STATUSES`, `ACTIVE`; fasi `STAGE_TRANSCRIBE`, `STAGE_ENRICH`; `PRIORITY_NORMAL = 100`, `PRIORITY_LOW = 10`; `BACKOFF` (1 min, 5 min, 30 min, 2 h, 6 h), `MAX_ATTEMPTS = 6`, `LEASE = 30 min`, `MAX_LAST_ERROR_LEN`, `INTERRUPTED`; `JobRunning`; `get_job(s, capture_id) -> Job | None` (rilettura forzata); `enqueue(s, capture_id, now, *, priority=PRIORITY_NORMAL, stage=STAGE_TRANSCRIBE) -> Job`; `is_active(s, capture_id) -> bool`; `requeue(s, capture, now) -> Job` (`JobRunning` se in corso con lease valido; fase `enrich` se la trascrizione è corretta a mano); `count_backfill(s) -> int`, `enqueue_backfill(s, now) -> int`; `record_failure(job, now, error, *, permanent=False)`; `claim(s, now) -> Job | None` (fa commit); `advance(s, capture_id, stage, now)`, `mark_done(s, capture_id, now)`, `release(s, capture_id, now, stage=None)`, `fail(s, capture_id, now, error, *, permanent) -> Job | None`, `drop(s, capture_id)`, `cancel_for_trash(s, capture_id)`, `queue_counts(s) -> dict[str, int]`. Tranne `claim`, nessuna fa commit.
  - `library.reprocess(s, capture_id, now) -> Job` (fa commit; `NotFound`, `InTrash`, `jobs.JobRunning`); `library.trash_capture` cancella il lavoro non in corso.
  - CLI: `secondbrain process <capture-id>` e `secondbrain process --backfill`.

Scelte (spec §7):
- "Note senza `.md`" per backfill = fuori dal cestino, `transcript IS NULL` nel catalogo e senza un lavoro già attivo (il catalogo è allineato al disco da `rescan`, Task 11, che controlla davvero i `.md`). Una nota `failed` rientra nel backfill: l'utente l'ha chiesto.
- Un lease scaduto conta come tentativo fallito (`INTERRUPTED`): una nota che fa morire il worker ogni volta finisce `failed` invece di girare per sempre.
- "Rielabora" su un lavoro in corso con lease valido è rifiutato (`JobRunning`): altrimenti il worker, finendo, lo segnerebbe `done` sopra la richiesta nuova.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_jobs.py` (il punto 4 della Review Focus è `test_new_notes_pass_the_backlog`; il ritentativo del device su una nota elaborata è `test_device_retry_of_a_processed_note_changes_nothing`):

```python
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select

from secondbrain import jobs, library
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.cli import main
from secondbrain.models import Capture, Job
from tests.helpers import DEV2, NOW, TEST_DB, capture_by, make_wav, upload

CID = "cap_20260923_191530"
OLD = ("cap_20260801_080000", "cap_20260802_080000")


def job_of(db, capture_id) -> Job | None:
    db.expire_all()
    return db.get(Job, capture_id)


def jobs_count(db) -> int:
    return db.scalar(select(func.count()).select_from(Job))


@pytest.fixture
def note(lan_client, db):
    upload(lan_client)
    return capture_by(db, CID).id


def test_ingest_queues_the_new_note(note, db):
    job = job_of(db, note)
    assert (job.status, job.stage, job.priority, job.attempts, job.next_run_at) == (
        jobs.QUEUED, jobs.STAGE_TRANSCRIBE, jobs.PRIORITY_NORMAL, 0, NOW)


def test_device_retry_of_a_processed_note_changes_nothing(note, lan_client, db):
    jobs.mark_done(db, note, NOW)
    db.commit()
    assert upload(lan_client).status_code == 409
    assert job_of(db, note).status == jobs.DONE
    assert jobs_count(db) == 1


def test_new_notes_pass_the_backlog(lan_client, db):
    """Review Focus 4: l'arretrato in coda da un'ora non fa aspettare una nota nuova."""
    for i, cid in enumerate(OLD):
        upload(lan_client, make_wav(fill=bytes([i + 2, 0])), capture_id=cid,
               ts=f"2026-08-0{i + 1}T08:00:00Z")
    old = {capture_by(db, cid).id for cid in OLD}
    for capture_id in old:
        jobs.enqueue(db, capture_id, NOW - timedelta(hours=1), priority=jobs.PRIORITY_LOW)
    db.commit()
    upload(lan_client)
    new = capture_by(db, CID).id
    assert jobs.claim(db, NOW).capture_id == new
    assert {jobs.claim(db, NOW).capture_id, jobs.claim(db, NOW).capture_id} == old
    assert jobs.claim(db, NOW) is None


def test_claim_takes_a_lease(note, db):
    job = jobs.claim(db, NOW)
    assert (job.status, job.locked_until) == (jobs.RUNNING, NOW + jobs.LEASE)
    assert jobs.claim(db, NOW + timedelta(minutes=1)) is None


def test_expired_lease_is_taken_again_and_counted(note, db):
    jobs.claim(db, NOW)
    later = NOW + jobs.LEASE + timedelta(seconds=1)
    job = jobs.claim(db, later)
    assert (job.capture_id, job.status, job.attempts, job.last_error) == (
        note, jobs.RUNNING, 1, jobs.INTERRUPTED)
    assert job.locked_until == later + jobs.LEASE


def test_a_job_that_keeps_crashing_the_worker_ends_failed(note, db):
    now = NOW
    for _ in range(jobs.MAX_ATTEMPTS):
        jobs.claim(db, now)
        now += jobs.LEASE + timedelta(seconds=1)
    assert jobs.claim(db, now) is None
    job = job_of(db, note)
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS)


def test_two_workers_never_get_the_same_job(lan_client, db, engine):
    upload(lan_client)
    upload(lan_client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260923_080000",
           ts="2026-09-23T08:00:00Z", device=DEV2)
    first, second = capture_by(db, CID).id, capture_by(db, "cap_20260923_080000").id
    other = make_sessionmaker(engine)()
    try:
        other.get(Job, first, with_for_update=True)  # l'altro worker sta prendendo questo
        assert jobs.claim(db, NOW).capture_id == second
    finally:
        other.rollback()
        other.close()


def test_backoff_then_failed():
    job = Job(attempts=0, status=jobs.RUNNING)
    for i, delay in enumerate(jobs.BACKOFF, start=1):
        jobs.record_failure(job, NOW, "groq: HTTP 429")
        assert (job.status, job.attempts, job.next_run_at) == (jobs.QUEUED, i, NOW + delay)
    jobs.record_failure(job, NOW, "groq: HTTP 429")
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS)


def test_permanent_failure_and_long_errors():
    job = Job(attempts=0, status=jobs.RUNNING)
    jobs.record_failure(job, NOW, "x" * 5000, permanent=True)
    assert (job.status, job.attempts, len(job.last_error)) == (
        jobs.FAILED, 1, jobs.MAX_LAST_ERROR_LEN)


def test_trash_cancels_the_job_and_restore_does_not_queue(note, db, settings):
    archive = Archive(settings.archive_dir)
    library.trash_capture(db, archive, note, NOW)
    assert job_of(db, note) is None
    library.restore_capture(db, archive, note)
    assert job_of(db, note) is None


def test_trash_leaves_a_running_job_to_the_worker(note, db, settings):
    jobs.claim(db, NOW)
    library.trash_capture(db, Archive(settings.archive_dir), note, NOW)
    assert job_of(db, note).status == jobs.RUNNING


def test_reprocess(note, db):
    jobs.mark_done(db, note, NOW)
    db.commit()
    job = library.reprocess(db, note, NOW)
    assert (job.status, job.stage, job.attempts) == (jobs.QUEUED, jobs.STAGE_TRANSCRIBE, 0)
    cap = db.get(Capture, note)
    cap.transcript, cap.edited = "corretto a mano", ["transcript"]
    db.commit()
    assert library.reprocess(db, note, NOW).stage == jobs.STAGE_ENRICH


def test_reprocess_refuses_a_running_job_but_not_a_dead_one(note, db):
    jobs.claim(db, NOW)
    with pytest.raises(jobs.JobRunning):
        library.reprocess(db, note, NOW + timedelta(minutes=1))
    db.rollback()
    assert library.reprocess(db, note, NOW + jobs.LEASE * 2).status == jobs.QUEUED


def test_reprocess_refused_in_trash(note, db, settings):
    library.trash_capture(db, Archive(settings.archive_dir), note, NOW)
    with pytest.raises(library.InTrash):
        library.reprocess(db, note, NOW)


def test_backfill_takes_notes_without_transcript(recordings, db, settings):
    db.execute(delete(Job))  # come le note archiviate prima dell'elaborazione AI
    db.commit()
    with_text = capture_by(db, CID)
    with_text.transcript = "già trascritta"
    db.commit()
    library.trash_capture(db, Archive(settings.archive_dir),
                          capture_by(db, "cap_20260923_080000").id, NOW)
    assert jobs.count_backfill(db) == 1
    assert jobs.enqueue_backfill(db, NOW) == 1
    db.commit()
    [job] = db.scalars(select(Job)).all()
    assert (job.capture_id, job.priority) == (capture_by(db, "cap_20260810_070000").id,
                                              jobs.PRIORITY_LOW)
    assert jobs.count_backfill(db) == 0


def test_deleting_a_capture_deletes_its_job(note, db, settings):
    archive = Archive(settings.archive_dir)
    library.trash_capture(db, archive, note, NOW)
    jobs.enqueue(db, note, NOW)
    db.commit()
    library.delete_capture(db, archive, note)
    assert jobs_count(db) == 0


def test_queue_counts(recordings, db):
    jobs.claim(db, NOW)
    assert jobs.queue_counts(db) == {"queued": 2, "running": 1, "done": 0, "failed": 0}


@pytest.fixture
def cli_env(monkeypatch, settings):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))


def test_cli_process(note, db, cli_env, capsys):
    jobs.mark_done(db, note, NOW)
    db.commit()
    assert main(["process", str(note)]) == 0
    assert "in coda (fase transcribe)" in capsys.readouterr().out
    assert job_of(db, note).status == jobs.QUEUED


def test_cli_process_backfill(note, db, cli_env, capsys):
    db.execute(delete(Job))
    db.commit()
    assert main(["process", "--backfill"]) == 0
    assert "Messe in coda 1 note" in capsys.readouterr().out
    assert job_of(db, note).priority == jobs.PRIORITY_LOW


@pytest.mark.parametrize("argv,message", [
    (["process"], "oppure --backfill"),
    (["process", "non-un-uuid"], "id non valido"),
    (["process", str(uuid.UUID(int=1))], "sconosciuta"),
])
def test_cli_process_errors(db, cli_env, capsys, argv, message):
    assert main(argv) == 1
    assert message in capsys.readouterr().err
```

Run: `uv run pytest tests/test_jobs.py -q`
Expected: FAIL (`ImportError: cannot import name 'jobs'`).

- [ ] **Step 2: `jobs.py`**

```python
"""Coda di elaborazione in Postgres: un lavoro per nota (spec §7).

Stati `queued` → `running` → `done` | `failed`; fasi `transcribe` → `enrich`. Il worker
prende il lavoro con `FOR UPDATE SKIP LOCKED` e un lease: se muore a metà, allo scadere
del lease il lavoro si riprende (contando il tentativo).
"""
import uuid
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from .models import Capture, Job

QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
STATUSES = (QUEUED, RUNNING, DONE, FAILED)
ACTIVE = (QUEUED, RUNNING)
STAGE_TRANSCRIBE = "transcribe"
STAGE_ENRICH = "enrich"
PRIORITY_NORMAL = 100  # note appena arrivate e "Rielabora"
PRIORITY_LOW = 10      # arretrato e rescan: passano dopo le note nuove
BACKOFF = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30),
           timedelta(hours=2), timedelta(hours=6))
MAX_ATTEMPTS = len(BACKOFF) + 1  # al sesto tentativo fallito la nota è failed
# Il lease deve superare il caso peggiore di un lavoro: tre provider in timeout sulla
# trascrizione (3 × 300 s) e sull'arricchimento (3 × 120 s) fanno 21 minuti.
LEASE = timedelta(minutes=30)
MAX_LAST_ERROR_LEN = 1000  # gli errori di più provider in fila, ognuno già limitato
INTERRUPTED = "elaborazione interrotta: il worker si è fermato a metà"


class JobRunning(Exception):
    """La nota è in elaborazione proprio ora: non si rimette in coda."""


def get_job(s: Session, capture_id: uuid.UUID) -> Job | None:
    return s.get(Job, capture_id, populate_existing=True)


def _locked(s: Session, capture_id: uuid.UUID) -> Job | None:
    return s.get(Job, capture_id, with_for_update=True, populate_existing=True)


def enqueue(s: Session, capture_id: uuid.UUID, now: datetime, *,
            priority: int = PRIORITY_NORMAL, stage: str = STAGE_TRANSCRIBE) -> Job:
    """Crea il lavoro o lo rimette da capo in coda (tentativi azzerati). Niente commit."""
    job = _locked(s, capture_id)
    if job is None:
        job = Job(capture_id=capture_id)
        s.add(job)
    job.status = QUEUED
    job.stage = stage
    job.priority = priority
    job.attempts = 0
    job.next_run_at = now
    job.locked_until = None
    job.last_error = None
    job.updated_at = now
    s.flush()
    return job


def is_active(s: Session, capture_id: uuid.UUID) -> bool:
    job = get_job(s, capture_id)
    return job is not None and job.status in ACTIVE


def requeue(s: Session, capture: Capture, now: datetime) -> Job:
    """"Rielabora": se la trascrizione è stata corretta a mano si riparte dall'arricchimento."""
    job = _locked(s, capture.id)
    if (job is not None and job.status == RUNNING and job.locked_until is not None
            and job.locked_until > now):
        raise JobRunning(str(capture.id))
    edited_transcript = "transcript" in (capture.edited or []) and capture.transcript is not None
    return enqueue(s, capture.id, now,
                   stage=STAGE_ENRICH if edited_transcript else STAGE_TRANSCRIBE)


def _backfill():
    """Note fuori dal cestino senza trascrizione (quindi senza `.md`) e non già in coda."""
    return (select(Capture.id).outerjoin(Job, Job.capture_id == Capture.id)
            .where(Capture.trashed_at.is_(None), Capture.transcript.is_(None),
                   or_(Job.status.is_(None), Job.status.not_in(ACTIVE))))


def count_backfill(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(_backfill().subquery()))


def enqueue_backfill(s: Session, now: datetime) -> int:
    ids = list(s.scalars(_backfill().order_by(Capture.recorded_at.desc())))
    for capture_id in ids:
        enqueue(s, capture_id, now, priority=PRIORITY_LOW)
    return len(ids)


def record_failure(job: Job, now: datetime, error: str, *, permanent: bool = False) -> None:
    job.attempts += 1
    job.last_error = error[:MAX_LAST_ERROR_LEN]
    job.locked_until = None
    job.updated_at = now
    if permanent or job.attempts >= MAX_ATTEMPTS:
        job.status = FAILED
    else:
        job.status = QUEUED
        job.next_run_at = now + BACKOFF[job.attempts - 1]


def claim(s: Session, now: datetime) -> Job | None:
    """Il lavoro con priorità più alta e attesa più lunga tra quelli pronti; fa commit."""
    due = or_(and_(Job.status == QUEUED, Job.next_run_at <= now),
              and_(Job.status == RUNNING, Job.locked_until < now))
    stmt = (select(Job).where(due).order_by(Job.priority.desc(), Job.next_run_at).limit(1)
            .with_for_update(skip_locked=True).execution_options(populate_existing=True))
    while True:
        job = s.scalars(stmt).first()
        if job is None:
            s.commit()
            return None
        if job.status == RUNNING:  # lease scaduto: il worker di prima si è fermato a metà
            record_failure(job, now, INTERRUPTED)
            if job.status == FAILED:
                s.commit()
                continue
        job.status = RUNNING
        job.locked_until = now + LEASE
        job.updated_at = now
        s.commit()
        return job


def advance(s: Session, capture_id: uuid.UUID, stage: str, now: datetime) -> None:
    """Fase successiva nello stesso giro del worker: lease rinnovato, tentativi azzerati."""
    job = _locked(s, capture_id)
    if job is not None:
        job.stage = stage
        job.attempts = 0
        job.last_error = None
        job.locked_until = now + LEASE
        job.updated_at = now


def mark_done(s: Session, capture_id: uuid.UUID, now: datetime) -> None:
    job = _locked(s, capture_id)
    if job is not None:
        job.status = DONE
        job.attempts = 0
        job.last_error = None
        job.locked_until = None
        job.updated_at = now


def release(s: Session, capture_id: uuid.UUID, now: datetime, stage: str | None = None) -> None:
    """Di nuovo in coda subito, senza contare un tentativo (arresto del worker)."""
    job = _locked(s, capture_id)
    if job is not None:
        job.status = QUEUED
        job.next_run_at = now
        job.locked_until = None
        job.updated_at = now
        if stage is not None:
            job.stage = stage


def fail(s: Session, capture_id: uuid.UUID, now: datetime, error: str, *,
         permanent: bool) -> Job | None:
    job = _locked(s, capture_id)
    if job is not None:
        record_failure(job, now, error, permanent=permanent)
    return job


def drop(s: Session, capture_id: uuid.UUID) -> None:
    s.execute(delete(Job).where(Job.capture_id == capture_id))


def cancel_for_trash(s: Session, capture_id: uuid.UUID) -> None:
    """Nel cestino: via il lavoro, salvo quello in corso (lo scarta il worker)."""
    s.execute(delete(Job).where(Job.capture_id == capture_id, Job.status != RUNNING))


def queue_counts(s: Session) -> dict[str, int]:
    counts = dict.fromkeys(STATUSES, 0)
    for status, n in s.execute(select(Job.status, func.count()).group_by(Job.status)):
        counts[status] = n
    return counts
```

- [ ] **Step 3: Lavoro creato all'ingest**

In `backend/secondbrain/ingest.py`: `from . import catalog, jobs, naming` al posto di `from . import catalog, naming`; in `_store` il blocco del commit diventa:

```python
        s.add(capture)
        try:
            # Il lavoro nasce nella stessa transazione della nota (spec AI §7): o ci sono
            # entrambi o nessuno dei due.
            jobs.enqueue(s, capture.id, now)
            _commit(s)
        except Exception:
```

(il resto del blocco `except` resta com'è: `enqueue` fa il flush della nota e del lavoro, quindi un errore del database lì dentro passa dalla stessa pulizia di un commit fallito; il `409` duplicato esce prima e non crea niente).

- [ ] **Step 4: Cestino e Rielabora in `library`**

In `backend/secondbrain/library.py`: `from . import jobs` prima di `from .archive import TRASH, Archive`, e `from .models import Capture, Job` al posto di `from .models import Capture`. In `trash_capture`:

```python
    if capture.trashed_at is None:
        capture.rel_path = archive.move(capture.rel_path, f"{TRASH}/{day_dir(capture.day)}")
        capture.trashed_at = now
        jobs.cancel_for_trash(s, capture.id)
        _save(s, archive, capture)
```

e prima di `delete_capture`:

```python
def reprocess(s: Session, capture_id: uuid.UUID, now: datetime) -> Job:
    """"Rielabora": di nuovo in coda da capo; i campi corretti a mano restano (spec AI §10)."""
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        raise InTrash("la registrazione è nel cestino")
    job = jobs.requeue(s, capture, now)
    s.commit()
    return job
```

(`restore_capture` non cambia: il ripristino non rimette in coda. `delete_capture` e `purge_trash` non cambiano: il lavoro sparisce con la nota per `ON DELETE CASCADE`.)

- [ ] **Step 5: Comando `process`**

In `backend/secondbrain/cli.py`: `import uuid` dopo `import sys`; `from . import catalog, devices, jobs, library, ota, settings_store`; prima di `_gen_key`:

```python
def _process(args: argparse.Namespace) -> None:
    if args.backfill == (args.id is not None):
        raise CliError("indica l'id di una registrazione oppure --backfill")
    with open_session() as (s, _):
        if args.backfill:
            n = jobs.enqueue_backfill(s, utcnow())
            s.commit()
            print(f"Messe in coda {n} note senza trascrizione (priorità bassa).")
            return
        try:
            capture_id = uuid.UUID(args.id)
        except ValueError:
            raise CliError(f"id non valido: {args.id!r}") from None
        try:
            job = library.reprocess(s, capture_id, utcnow())
        except library.NotFound:
            raise CliError(f"registrazione {capture_id} sconosciuta") from None
        except library.InTrash as exc:
            raise CliError(str(exc)) from None
        except jobs.JobRunning:
            raise CliError("la nota è in elaborazione proprio ora: riprova tra poco") from None
    print(f"Registrazione {capture_id} in coda (fase {job.stage}).")


def _add_process_command(sub) -> None:
    cmd = sub.add_parser("process", help="mette in coda l'elaborazione AI di una registrazione")
    cmd.add_argument("id", nargs="?", help="id della registrazione (UUID)")
    cmd.add_argument("--backfill", action="store_true",
                     help="tutte le note fuori dal cestino senza trascrizione, priorità bassa")
    cmd.set_defaults(func=_process)
```

e `_add_process_command` in fondo a `COMMAND_GROUPS`.

- [ ] **Step 6: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/secondbrain/jobs.py backend/secondbrain/ingest.py backend/secondbrain/library.py backend/secondbrain/cli.py backend/tests/test_jobs.py
git commit -m "backend: coda di elaborazione in Postgres, creata all'arrivo della nota

Un lavoro per nota, nella stessa transazione dell'ingest: un 409 duplicato
non crea niente. Il worker prenderà il lavoro con SKIP LOCKED e un lease di
30 minuti; le note nuove passano davanti all'arretrato, i fallimenti tornano
in coda dopo 1 min, 5 min, 30 min, 2 h, 6 h e al sesto la nota è failed.
Il cestino cancella il lavoro non in corso; process rimette in coda una nota
o tutto l'arretrato."
```

---

### Task 9: Scrittura dei campi AI — file della nota, indice di ricerca, lock di riga, correzioni

**Files:**
- Modify: `backend/secondbrain/archive.py` (`NOTE_SUFFIX`, `AI_SUFFIX`, `_write_atomic`, `derived_path`, `write_text`, `read_text`)
- Create: `backend/secondbrain/search.py` (per ora solo `refresh_search_vector`)
- Create: `backend/secondbrain/notes.py`
- Modify: `backend/secondbrain/library.py` (`_get` con lock, `_save` con indice, `edit_ai_field`)
- Test: `backend/tests/test_archive.py`, `backend/tests/test_notes.py`

**Interfaces:**
- Consumes: `Note`, `render_note`, `apply_edit`, `EDITABLE_FIELDS` (Task 2); `text_search_config` (Task 2); colonne AI e `search_vector` (Task 1); `library._get`, `_save`, `NotFound`, `InTrash` (esistenti).
- Produces:
  - `archive`: `NOTE_SUFFIX = ".md"`, `AI_SUFFIX = ".ai.json"`; `Archive.derived_path(rel_wav, suffix) -> Path`, `Archive.write_text(rel_wav, suffix, text)` (atomica via `.incoming/`), `Archive.read_text(rel_wav, suffix) -> str` (`FileNotFoundError` se manca). `write_sidecar` passa dallo stesso `_write_atomic` (formato del sidecar invariato).
  - `search.refresh_search_vector(s, capture) -> None` (flush + `UPDATE` con un'unica espressione, niente commit).
  - `notes`: `EMPTY_AI_FIELDS: dict`, `lock_capture(s, capture_id) -> Capture | None` (`SELECT … FOR UPDATE`, rilettura forzata), `note_from_capture(capture) -> Note`, `note_fields(note) -> dict` (colonne del catalogo), `write_note(s, archive, capture, note, tz)` (`.md` poi colonne poi indice; niente commit).
  - `library`: ogni operazione prende la riga con `lock_capture` (titolo, data, cestino, ripristino, eliminazione, Rielabora); `_save` aggiorna l'indice (il titolo manuale è cercabile); `edit_ai_field(s, archive, tz, capture_id, field, value) -> Capture` (fa commit; `ValueError` campo non modificabile, `NotFound`, `InTrash`).

Perché il lock anche su titolo, data e cestino: "correggi data/ora" e il cestino spostano tutti i `<base>.*`; se il worker scrivesse il `.md` nella cartella vecchia mentre la UI sposta i file, il `.md` resterebbe orfano. Con il lock uno dei due aspetta l'altro e il worker rilegge `rel_path` dopo averlo preso (spec §11).

- [ ] **Step 1: Test dell'archivio (falliscono)**

In fondo a `backend/tests/test_archive.py`:

```python
def test_write_text_writes_note_files_atomically(archive):
    put(archive, f"{DAY}/x.wav")
    archive.write_text(f"{DAY}/x.wav", ".md", "---\nedited: []\n---\n")
    archive.write_text(f"{DAY}/x.wav", ".ai.json", "{}\n")
    archive.write_text(f"{DAY}/x.wav", ".md", "---\nedited: [tags]\n---\n")
    assert archive.read_text(f"{DAY}/x.wav", ".md") == "---\nedited: [tags]\n---\n"
    assert archive.derived_path(f"{DAY}/x.wav", ".ai.json") == archive.root / DAY / "x.ai.json"
    assert archive.related_files(f"{DAY}/x.wav") == ["x.ai.json", "x.md", "x.wav"]
    assert list(archive.incoming_dir.iterdir()) == []
    assert list(archive.iter_sidecar_rels()) == []  # .ai.json non è mai un sidecar


def test_move_carries_the_note_files(archive):
    for name in ["x.wav", "x.json", "x.md", "x.ai.json"]:
        put(archive, f"{DAY}/{name}")
    put(archive, "2026/10/01/x.wav")
    new = archive.move(f"{DAY}/x.wav", "2026/10/01")
    assert archive.related_files(new) == ["x_2.ai.json", "x_2.json", "x_2.md", "x_2.wav"]


def test_write_text_cleans_up_its_tmp_on_failure(archive, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disco pieno")

    put(archive, f"{DAY}/x.wav")
    monkeypatch.setattr("secondbrain.archive.os.replace", boom)
    with pytest.raises(OSError):
        archive.write_text(f"{DAY}/x.wav", ".md", "testo")
    assert list(archive.incoming_dir.iterdir()) == []
```

- [ ] **Step 2: Test di note, correzioni e lock (falliscono)**

`backend/tests/test_notes.py`:

```python
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from secondbrain import library
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.models import Capture
from secondbrain.notefile import Note, parse_note
from secondbrain.notes import lock_capture, note_from_capture, write_note
from tests.helpers import NOW, capture_by

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"
NOTE = Note(transcript="Devo chiamare Marco per il preventivo", title="Chiamare Marco",
            summary="Preventivo del tetto.", tags=("lavoro",), language="it", provider="groq",
            transcribe_model="whisper-large-v3-turbo", enrich_provider="groq",
            enrich_model="openai/gpt-oss-120b", processed_at=NOW)


@pytest.fixture
def arch(settings):
    return Archive(settings.archive_dir)


def fresh(db, capture_id) -> Capture:
    db.expire_all()
    return db.get(Capture, capture_id)


def matches(db, capture_id, words: str) -> bool:
    return db.scalar(text("SELECT search_vector @@ websearch_to_tsquery('italian', :q) "
                          "FROM captures WHERE id = :id"), {"q": words, "id": capture_id})


def test_write_note_updates_disk_catalog_and_search(recordings, db, arch):
    cap = capture_by(db, CID)
    write_note(db, arch, cap, NOTE, ROME)
    db.commit()
    assert parse_note(arch.read_text(cap.rel_path, ".md")) == NOTE
    cap = fresh(db, cap.id)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.ai_provider) == (
        NOTE.transcript, "Chiamare Marco", "Preventivo del tetto.", ["lavoro"], "groq")
    assert note_from_capture(cap) == NOTE
    assert matches(db, cap.id, "chiamato") and matches(db, cap.id, "lavoro")
    assert not matches(db, cap.id, "spesa")


def test_manual_title_is_searchable(recordings, db, arch):
    cap = library.set_title(db, arch, capture_by(db, CID).id, "Idea per il digest")
    assert matches(db, cap.id, "digest")


def test_edit_summary_marks_it_edited(recordings, db, arch):
    cap = capture_by(db, CID)
    write_note(db, arch, cap, NOTE, ROME)
    db.commit()
    library.edit_ai_field(db, arch, ROME, cap.id, "summary", "  Mio   riassunto ")
    cap = fresh(db, cap.id)
    assert (cap.summary, cap.edited, cap.title_auto) == ("Mio riassunto", ["summary"], "Chiamare Marco")
    on_disk = parse_note(arch.read_text(cap.rel_path, ".md"))
    assert (on_disk.summary, on_disk.edited) == ("Mio riassunto", ("summary",))
    assert matches(db, cap.id, "riassunto")


def test_edit_tags_and_transcript(recordings, db, arch):
    cap_id = capture_by(db, CID).id
    library.edit_ai_field(db, arch, ROME, cap_id, "tags", "Lavoro, #casa")
    library.edit_ai_field(db, arch, ROME, cap_id, "transcript", " Testo corretto a mano ")
    cap = fresh(db, cap_id)
    assert (cap.tags, cap.transcript, cap.edited) == (
        ["lavoro", "casa"], "Testo corretto a mano", ["transcript", "tags"])


def test_edit_refused_in_trash_unknown_field_or_capture(recordings, db, arch):
    cap_id = capture_by(db, CID).id
    with pytest.raises(ValueError):
        library.edit_ai_field(db, arch, ROME, cap_id, "title", "x")
    with pytest.raises(library.NotFound):
        library.edit_ai_field(db, arch, ROME, uuid.uuid4(), "summary", "x")
    library.trash_capture(db, arch, cap_id, NOW)
    with pytest.raises(library.InTrash):
        library.edit_ai_field(db, arch, ROME, cap_id, "summary", "x")


def test_edits_wait_for_the_row_lock_held_by_the_worker(recordings, db, engine, arch):
    cap_id = capture_by(db, CID).id
    db.commit()
    worker = make_sessionmaker(engine)()
    try:
        assert lock_capture(worker, cap_id) is not None  # il worker sta scrivendo la nota
        for action in (lambda: library.edit_ai_field(db, arch, ROME, cap_id, "summary", "x"),
                       lambda: library.set_title(db, arch, cap_id, "x")):
            db.execute(text("SET lock_timeout = '200ms'"))
            with pytest.raises(OperationalError):
                action()
            db.rollback()
    finally:
        worker.rollback()
        worker.close()
    assert fresh(db, cap_id).summary is None
```

Run: `uv run pytest tests/test_archive.py tests/test_notes.py -q`
Expected: FAIL (`AttributeError: 'Archive' object has no attribute 'write_text'`, `ModuleNotFoundError: No module named 'secondbrain.notes'`).

- [ ] **Step 3: Archivio**

In `backend/secondbrain/archive.py`, dopo `SIDECAR_SUFFIX = ".json"`:

```python
NOTE_SUFFIX = ".md"         # nota leggibile, verità per i campi AI (spec AI §6)
AI_SUFFIX = ".ai.json"      # risposte grezze dei provider: due punti, quindi mai un sidecar
```

e `write_sidecar` diventa (con i tre metodi nuovi subito dopo):

```python
    def _write_atomic(self, target: Path, data: bytes, suffix: str) -> None:
        """Scrittura in `.incoming/` e rename: chi legge vede il file vecchio o quello nuovo."""
        self.ensure()
        tmp = self.incoming_dir / f"{uuid.uuid4().hex}{suffix}.tmp"
        try:
            with open(tmp, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def write_sidecar(self, rel_wav: str, data: dict) -> None:
        text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self._write_atomic(self.abs(rel_wav).with_suffix(SIDECAR_SUFFIX), text.encode("utf-8"),
                           SIDECAR_SUFFIX)

    def derived_path(self, rel_wav: str, suffix: str) -> Path:
        """`<base><suffix>` accanto al WAV, es. `<base>.md`."""
        path = self.abs(rel_wav)
        return path.with_name(path.stem + suffix)

    def write_text(self, rel_wav: str, suffix: str, text: str) -> None:
        self._write_atomic(self.derived_path(rel_wav, suffix), text.encode("utf-8"), suffix)

    def read_text(self, rel_wav: str, suffix: str) -> str:
        return self.derived_path(rel_wav, suffix).read_text(encoding="utf-8")
```

`move`, `related_files`, `delete` non cambiano: lavorano già su tutti i `<base>.*`; `iter_sidecar_rels` salta già i nomi con più di un punto (`_is_base_file`), quindi `<base>.ai.json` non è mai scambiato per un sidecar.

- [ ] **Step 4: `search.py`**

```python
"""Ricerca full-text sulle note (spec §10).

`search_vector` si ricalcola dal codice a ogni scrittura dei campi con un'unica
espressione: peso A il titolo mostrato (manuale o automatico), B tag e riassunto,
C la trascrizione, nella configurazione di testo della lingua della nota.
"""
from sqlalchemy import text
from sqlalchemy.orm import Session

from .languages import text_search_config
from .models import Capture

_REFRESH = text("""
UPDATE captures SET search_vector =
       setweight(to_tsvector(CAST(:cfg AS regconfig), coalesce(title, title_auto, '')), 'A')
    || setweight(to_tsvector(CAST(:cfg AS regconfig),
                             array_to_string(tags, ' ') || ' ' || coalesce(summary, '')), 'B')
    || setweight(to_tsvector(CAST(:cfg AS regconfig), coalesce(transcript, '')), 'C')
WHERE id = :id
""")


def refresh_search_vector(s: Session, capture: Capture) -> None:
    """Da chiamare dopo ogni modifica di titolo o campi AI, prima del commit."""
    s.flush()
    s.execute(_REFRESH, {"cfg": text_search_config(capture.language), "id": capture.id})
```

- [ ] **Step 5: `notes.py`**

```python
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
```

- [ ] **Step 6: `library`**

In `backend/secondbrain/library.py`, import dopo `from .naming import day_dir, local_day`:

```python
from .notefile import EDITABLE_FIELDS, apply_edit
from .notes import lock_capture, note_from_capture, write_note
from .search import refresh_search_vector
```

`_get` e `_save` diventano:

```python
def _get(s: Session, capture_id: uuid.UUID) -> Capture:
    """La riga resta bloccata fino al commit: le operazioni su file e campi di una nota non
    si accavallano con la scrittura del worker (spec AI §10)."""
    capture = lock_capture(s, capture_id)
    if capture is None:
        raise NotFound(str(capture_id))
    return capture


def _save(s: Session, archive: Archive, capture: Capture) -> None:
    archive.write_sidecar(capture.rel_path, capture_to_sidecar(capture))
    refresh_search_vector(s, capture)  # il titolo manuale pesa nella ricerca
    s.commit()
```

e prima di `reprocess`:

```python
def edit_ai_field(s: Session, archive: Archive, tz: ZoneInfo, capture_id: uuid.UUID, field: str,
                  value: str) -> Capture:
    """Correzione a mano di trascrizione, riassunto o tag: `.md`, catalogo, `edited`."""
    if field not in EDITABLE_FIELDS:
        raise ValueError(f"campo non modificabile: {field!r}")
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        raise InTrash("la registrazione è nel cestino")
    write_note(s, archive, capture, apply_edit(note_from_capture(capture), field, value), tz)
    s.commit()
    return capture
```

- [ ] **Step 7: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS (anche i test esistenti di `library`: il lock è della sessione che poi fa commit).

- [ ] **Step 8: Commit**

```bash
git add backend/secondbrain/archive.py backend/secondbrain/search.py backend/secondbrain/notes.py backend/secondbrain/library.py backend/tests/test_archive.py backend/tests/test_notes.py
git commit -m "backend: nota .md scritta con catalogo e indice, sotto lock di riga

Il .md e l'.ai.json si scrivono atomici come il sidecar e seguono la nota
in cestino e cambi di data come ogni <base>.*. Ogni scrittura dei campi AI
o del titolo aggiorna l'indice di ricerca con un'unica espressione (titolo
peso A, tag e riassunto B, trascrizione C). UI e worker scrivono tenendo il
lock sulla riga della nota e rileggendola: una correzione fatta a mano non
può essere sovrascritta da un'elaborazione che arriva un attimo dopo."
```

---

### Task 10: Worker — due fasi, fallback, backoff, arresto pulito, `secondbrain worker`

**Files:**
- Create: `backend/secondbrain/worker.py`
- Modify: `backend/secondbrain/cli.py` (comando `worker`)
- Modify: `backend/tests/ai_fakes.py` (`FAKE_KEYS`, `configure_providers`)
- Test: `backend/tests/test_worker.py`

**Interfaces:**
- Consumes: `jobs.*` (Task 8); `notes.lock_capture`, `note_from_capture`, `write_note` (Task 9); `notefile.apply_transcript`, `apply_enrichment` (Task 2); `settings_store.SecretBox`, `is_paused`, `get_language`, `provider_configs` (Task 3); `chain.transcribe`, `chain.enrich`, `AllProvidersFailed`, `ProviderFactory` (Task 7); `AudioSource`, `AudioError`, `ContentError` (Task 4); `record_usage`, `month_start` (Task 7); `Archive.write_text`, `read_text`, `AI_SUFFIX` (Task 9); `build_provider` (Task 6).
- Produces:
  - `worker`: `LOG_FORMAT`, `POLL_INTERVAL_S = 5.0`, `Shutdown(BaseException)`; dataclass `Worker(sessionmaker, archive, tz, box, factory, clock=utcnow)` con `run_once() -> bool` e `process(capture_id, configs, language)`; `run_forever(worker, stop: threading.Event, interval=POLL_INTERVAL_S)`; `install_signal_handlers(stop)`.
  - Formato di `<base>.ai.json`: `{"schema_version": 1, "transcribe": {"provider", "model", "at", "response"}, "enrich": {…}}`; la fase `transcribe` lo riscrive da zero ("Rielabora lo sostituisce"), la fase `enrich` aggiorna solo la sua sezione.
  - CLI: `secondbrain worker`.
  - `tests.ai_fakes`: `FAKE_KEYS`, `configure_providers(db, box, now, names=("groq", "gemini"))`.

Comportamento (spec §7, §11):
- Dorme (niente claim) se manca `SETTINGS_KEY`, se l'elaborazione è in pausa o se nessun provider è utilizzabile; logga il motivo solo quando cambia. Impostazioni, ordine e lingua si rileggono a ogni lavoro.
- Le chiamate ai provider avvengono senza lock e senza sessioni aperte; il risultato si scrive in una transazione nuova sotto `lock_capture`, ricavando il percorso dal catalogo in quel momento. Nota nel cestino o sparita → risultato scartato, lavoro cancellato.
- `ContentError`/`AudioError` → `failed` subito; `AllProvidersFailed` → backoff; qualunque altro errore locale (disco pieno, database) → backoff con `last_error = "errore locale: <tipo>"` e traceback nel log.
- SIGTERM/SIGINT sollevano `Shutdown` anche dentro una chiamata HTTP: il lavoro torna `queued` senza contare un tentativo e il processo esce (così `docker compose stop` non aspetta minuti).

- [ ] **Step 1: Helper e test (falliscono)**

In fondo a `backend/tests/ai_fakes.py` (e negli import in cima `from secondbrain import settings_store as store` e `from secondbrain.ai.registry import PROVIDER_SPECS`):

```python
FAKE_KEYS = {"groq": "gsk_test_groq_0123456789", "gemini": "AIza-test-gemini-0123456789abcdefghij",
             "openai": "sk-test-openai-0123456789"}


def configure_providers(db, box, now, names=("groq", "gemini")) -> None:
    """Provider con una chiave finta e i modelli di default, nell'ordine di default."""
    store.ensure_providers(db, now)
    for name in names:
        spec = PROVIDER_SPECS[name]
        store.save_provider(db, box, name, api_key=FAKE_KEYS[name],
                            transcribe_model=spec.transcribe_model, text_model=spec.text_model,
                            enabled=True, now=now)
    db.commit()
```

`backend/tests/test_worker.py` (i punti 1 e 5 della Review Focus sono `test_user_edit_during_enrichment_is_kept` e `test_rate_limit_storm_backs_off_then_fails`):

```python
import errno
import json
import threading
from datetime import date, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from secondbrain import jobs, library
from secondbrain import settings_store as store
from secondbrain.ai.base import ContentError, ServiceError
from secondbrain.archive import Archive
from secondbrain.catalog import make_sessionmaker
from secondbrain.cli import main
from secondbrain.models import AiProvider, Capture, Job
from secondbrain.notefile import parse_note
from secondbrain.usage import usage_for_month
from secondbrain.worker import Shutdown, Worker, run_forever
from tests.ai_fakes import DEFAULT_TEXT, FakeFactory, FakeProvider, configure_providers
from tests.helpers import DEV2, NOW, TEST_DB, TEST_SETTINGS_KEY, capture_by, make_wav, upload

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"


@pytest.fixture
def env(lan_client, db, settings, clock):
    box = store.SecretBox(settings.settings_key)
    configure_providers(db, box, NOW)
    upload(lan_client)
    groq, gemini = FakeProvider("groq"), FakeProvider("gemini")
    archive = Archive(settings.archive_dir)
    worker = Worker(sessionmaker=make_sessionmaker(db.get_bind()), archive=archive,
                    tz=settings.tz_archive, box=box, factory=FakeFactory(groq=groq, gemini=gemini),
                    clock=clock)
    return SimpleNamespace(worker=worker, groq=groq, gemini=gemini, box=box, archive=archive,
                           clock=clock, cap_id=capture_by(db, CID).id, client=lan_client)


def fresh(db, env) -> Capture:
    db.expire_all()
    return db.get(Capture, env.cap_id)


def job_of(db, env) -> Job | None:
    db.expire_all()
    return db.get(Job, env.cap_id)


def other_session(db):
    return make_sessionmaker(db.get_bind())()


def stages(provider, stage):
    return [call for call in provider.calls if call[0] == stage]


def test_processes_a_new_note(env, db):
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.language) == (
        DEFAULT_TEXT, "Chiamare Marco", "Chiamare Marco per il preventivo.", ["lavoro", "casa"], "it")
    assert (cap.ai_provider, cap.ai_transcribe_model, cap.ai_enrich_provider, cap.ai_enrich_model,
            cap.processed_at) == ("groq", "groq-stt", "groq", "groq-llm", NOW)
    note = parse_note(env.archive.read_text(cap.rel_path, ".md"))
    assert (note.title, note.transcript, note.tags) == ("Chiamare Marco", DEFAULT_TEXT,
                                                        ("lavoro", "casa"))
    raw = json.loads(env.archive.read_text(cap.rel_path, ".ai.json"))
    assert raw["transcribe"]["provider"] == "groq" and raw["transcribe"]["response"] == {
        "text": DEFAULT_TEXT}
    assert raw["enrich"]["model"] == "groq-llm"
    assert job_of(db, env).status == jobs.DONE
    usage = usage_for_month(db, date(2026, 9, 1))["groq"]
    assert (usage.audio_seconds, usage.calls) == (pytest.approx(1.0), 2)
    assert env.groq.calls == [("transcribe", "it"), ("enrich", DEFAULT_TEXT)]
    assert env.gemini.calls == []
    assert not env.worker.run_once()


def test_rate_limited_primary_falls_back_to_the_reserve(env, db):
    env.groq.transcripts = [ServiceError("groq", "HTTP 429: Rate limit")]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.ai_provider, cap.ai_enrich_provider) == ("gemini", "groq")  # ogni fase da capo
    usage = usage_for_month(db, date(2026, 9, 1))
    assert (usage["groq"].audio_seconds, usage["groq"].calls) == (0.0, 2)
    assert (usage["gemini"].audio_seconds, usage["gemini"].calls) == (pytest.approx(1.0), 1)


def test_rate_limit_storm_backs_off_then_fails(env, db):
    """Review Focus 5: tutti i provider in 429 → backoff, niente raffiche, poi failed."""
    env.groq.transcripts = [ServiceError("groq", "HTTP 429: quota esaurita")] * jobs.MAX_ATTEMPTS
    env.gemini.transcripts = [ServiceError("gemini", "HTTP 429: RESOURCE_EXHAUSTED")] * jobs.MAX_ATTEMPTS
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.next_run_at) == (
        jobs.QUEUED, 1, NOW + jobs.BACKOFF[0])
    assert not env.worker.run_once()  # prima del backoff non si ritenta
    assert len(env.groq.calls) == 1
    for _ in range(jobs.MAX_ATTEMPTS - 1):
        env.clock.now = job_of(db, env).next_run_at
        assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts) == (jobs.FAILED, jobs.MAX_ATTEMPTS)
    assert "groq: HTTP 429" in job.last_error and "gemini: HTTP 429" in job.last_error
    env.clock.now += timedelta(days=1)
    assert not env.worker.run_once()
    assert len(env.groq.calls) == jobs.MAX_ATTEMPTS


def test_content_error_fails_at_once(env, db):
    env.groq.transcripts = [ContentError("groq", "HTTP 400: audio non valido")]
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.last_error) == (
        jobs.FAILED, 1, "groq: HTTP 400: audio non valido")
    assert env.gemini.calls == []


def test_unreadable_audio_fails_at_once(env, db):
    env.archive.abs(fresh(db, env).rel_path).write_bytes(b"RIFFxxxxWAVEjunk")
    assert env.worker.run_once()
    job = job_of(db, env)
    assert job.status == jobs.FAILED and "illeggibile" in job.last_error
    assert env.groq.calls == []


def test_enrich_failure_does_not_transcribe_again(env, db):
    env.groq.enrichments = [ServiceError("groq", "HTTP 503")]
    env.gemini.enrichments = [ServiceError("gemini", "timeout")]
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.stage, job.attempts) == (jobs.QUEUED, jobs.STAGE_ENRICH, 1)
    assert fresh(db, env).transcript == DEFAULT_TEXT
    env.clock.now = job.next_run_at
    assert env.worker.run_once()
    assert job_of(db, env).status == jobs.DONE
    assert len(stages(env.groq, "transcribe")) == 1


def test_empty_transcript_is_done_without_enrichment(env, db):
    env.groq.transcripts = [""]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.transcript, cap.title_auto) == ("", None)
    assert parse_note(env.archive.read_text(cap.rel_path, ".md")).transcript == ""
    assert job_of(db, env).status == jobs.DONE
    assert stages(env.groq, "enrich") == []


def test_sleeps_when_paused_without_providers_or_without_key(env, db, settings, clock):
    store.set_paused(db, True)
    db.commit()
    assert not env.worker.run_once()
    store.set_paused(db, False)
    for row in db.scalars(select(AiProvider)):
        row.api_key_enc = None
    db.commit()
    assert not env.worker.run_once()
    no_key = Worker(sessionmaker=make_sessionmaker(db.get_bind()), archive=env.archive,
                    tz=ROME, box=store.SecretBox(None), factory=FakeFactory(), clock=clock)
    assert not no_key.run_once()
    assert job_of(db, env).status == jobs.QUEUED and env.groq.calls == []


def test_trash_during_transcription_discards_the_result(env, db):
    def trash_meanwhile():
        with other_session(db) as ui:
            library.trash_capture(ui, env.archive, env.cap_id, NOW)
        return DEFAULT_TEXT

    env.groq.transcripts = [trash_meanwhile]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert cap.trashed_at == NOW and cap.transcript is None
    assert not env.archive.derived_path(cap.rel_path, ".md").exists()
    assert job_of(db, env) is None
    assert stages(env.groq, "enrich") == []


def test_user_edit_during_enrichment_is_kept(env, db):
    """Review Focus 1: la correzione fatta mentre il modello risponde non si perde."""
    def user_edits_meanwhile():
        with other_session(db) as ui:
            library.edit_ai_field(ui, env.archive, ROME, env.cap_id, "summary",
                                  "Riassunto scritto da me")
        return ("Chiamare Marco", "Riassunto del modello", ("lavoro",))

    env.groq.enrichments = [user_edits_meanwhile]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.summary, cap.edited, cap.title_auto, cap.tags) == (
        "Riassunto scritto da me", ["summary"], "Chiamare Marco", ["lavoro"])
    note = parse_note(env.archive.read_text(cap.rel_path, ".md"))
    assert (note.summary, note.edited) == ("Riassunto scritto da me", ("summary",))


def test_reprocess_keeps_the_edited_fields(env, db):
    env.worker.run_once()
    library.edit_ai_field(db, env.archive, ROME, env.cap_id, "tags", "mio, tag")
    library.reprocess(db, env.cap_id, NOW)
    env.groq.enrichments = [("Nuovo titolo", "Nuovo riassunto", ("altro",))]
    assert env.worker.run_once()
    cap = fresh(db, env)
    assert (cap.title_auto, cap.summary, cap.tags, cap.edited) == (
        "Nuovo titolo", "Nuovo riassunto", ["mio", "tag"], ["tags"])
    assert len(stages(env.groq, "transcribe")) == 2


def test_reprocess_after_editing_the_transcript_skips_transcription(env, db):
    env.worker.run_once()
    library.edit_ai_field(db, env.archive, ROME, env.cap_id, "transcript", "Testo corretto a mano")
    assert library.reprocess(db, env.cap_id, NOW).stage == jobs.STAGE_ENRICH
    assert env.worker.run_once()
    assert env.groq.calls[-1] == ("enrich", "Testo corretto a mano")
    assert len(stages(env.groq, "transcribe")) == 1
    assert fresh(db, env).transcript == "Testo corretto a mano"


def test_settings_are_read_again_for_every_job(env, db):
    env.worker.run_once()
    store.move_provider(db, "gemini", store.MOVE_UP, NOW)
    store.set_language(db, "en")
    db.commit()
    upload(env.client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260923_080000",
           ts="2026-09-23T08:00:00Z", device=DEV2)
    assert env.worker.run_once()
    second = capture_by(db, "cap_20260923_080000")
    assert (second.ai_provider, second.language) == ("gemini", "en")


def test_shutdown_puts_the_job_back(env, db):
    env.groq.transcripts = [Shutdown()]
    with pytest.raises(Shutdown):
        env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.locked_until) == (jobs.QUEUED, 0, None)


def test_disk_error_goes_back_to_the_queue(env, db, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "disco pieno")

    monkeypatch.setattr(Archive, "write_text", full)
    assert env.worker.run_once()
    job = job_of(db, env)
    assert (job.status, job.attempts, job.last_error) == (
        jobs.QUEUED, 1, "errore locale: OSError")
    assert fresh(db, env).transcript is None


def test_run_forever_stops():
    stop = threading.Event()

    class Once:
        calls = 0

        def run_once(self):
            self.calls += 1
            stop.set()
            return False

    once = Once()
    run_forever(once, stop, interval=0)
    assert once.calls == 1

    class Interrupted:
        def run_once(self):
            raise Shutdown

    run_forever(Interrupted(), threading.Event(), interval=0)  # esce senza sollevare


def test_cli_worker_wiring(monkeypatch, settings):
    seen = {}
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    monkeypatch.setenv("SETTINGS_KEY", TEST_SETTINGS_KEY)
    monkeypatch.setattr("secondbrain.cli.install_signal_handlers", lambda stop: None)
    monkeypatch.setattr("secondbrain.cli.run_forever",
                        lambda worker, stop: seen.update(worker=worker))
    assert main(["worker"]) == 0
    assert isinstance(seen["worker"], Worker) and seen["worker"].box.available
```

Run: `uv run pytest tests/test_worker.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'secondbrain.worker'`).

- [ ] **Step 2: `worker.py`**

```python
"""Worker dell'elaborazione AI (spec §4, §7): un lavoro alla volta dalla coda in Postgres.

Fase `transcribe`: FLAC temporaneo, catena dei provider, `.md` con il solo corpo e
`.ai.json`, poi `enrich`. Fase `enrich`: testo dal catalogo, catena, titolo, riassunto e
tag nel `.md` e nel catalogo, `done`. Le chiamate ai provider avvengono senza lock; il
risultato si scrive sotto il lock della riga, rileggendo percorso, cestino ed `edited`.
"""
import json
import logging
import signal
import tempfile
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from . import jobs, notefile, notes
from . import settings_store as store
from .ai import chain
from .ai.audio import AudioError, AudioSource
from .ai.base import ContentError
from .ai.registry import ProviderConfig
from .archive import AI_SUFFIX, Archive
from .clock import utcnow
from .models import Capture
from .usage import month_start, record_usage

log = logging.getLogger(__name__)

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
POLL_INTERVAL_S = 5.0
AI_SCHEMA_VERSION = 1
TEMP_PREFIX = "secondbrain-audio-"
IDLE_NO_KEY = "SETTINGS_KEY non impostata"
IDLE_PAUSED = "elaborazione in pausa"
IDLE_NO_PROVIDER = "nessun provider abilitato con una chiave"


class Shutdown(BaseException):
    """Arresto richiesto (SIGTERM/SIGINT): il lavoro in corso torna in coda.

    BaseException, così nessun `except Exception` lo inghiotte a metà strada.
    """


def _ai_record(provider: str, model: str, at: datetime, raw: dict[str, Any]) -> dict:
    return {"provider": provider, "model": model, "at": at.isoformat(), "response": raw}


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


@dataclass
class Worker:
    sessionmaker: sessionmaker[Session]
    archive: Archive
    tz: ZoneInfo
    box: store.SecretBox
    factory: chain.ProviderFactory
    clock: Callable[[], datetime] = utcnow
    _idle_reason: str | None = field(default=None, init=False)

    def run_once(self) -> bool:
        """Elabora al più un lavoro; False se non c'era niente da fare o non si può."""
        with self.sessionmaker() as s:
            reason, configs = self._check(s)
            if reason is not None:
                self._idle(reason)
                return False
            language = store.get_language(s)  # impostazioni rilette a ogni lavoro
            job = jobs.claim(s, self.clock())
            if job is None:
                return False
            capture_id = job.capture_id
        self._idle(None)
        self.process(capture_id, configs, language)
        return True

    def _check(self, s: Session) -> tuple[str | None, list[ProviderConfig]]:
        if not self.box.available:
            return IDLE_NO_KEY, []
        if store.is_paused(s):
            return IDLE_PAUSED, []
        configs = store.provider_configs(s, self.box)
        return (None, configs) if configs else (IDLE_NO_PROVIDER, [])

    def _idle(self, reason: str | None) -> None:
        if reason is not None and reason != self._idle_reason:
            log.info("worker fermo: %s", reason)
        self._idle_reason = reason

    def process(self, capture_id: uuid.UUID, configs: list[ProviderConfig],
                language: str) -> None:
        try:
            stage = self._stage(capture_id)
            if stage is None:
                return
            if stage == jobs.STAGE_TRANSCRIBE and not self._transcribe(capture_id, configs,
                                                                       language):
                return
            self._enrich(capture_id, configs, language)
        except Shutdown:
            self._release(capture_id)
            raise
        except (ContentError, AudioError) as exc:
            self._fail(capture_id, str(exc), permanent=True)
        except chain.AllProvidersFailed as exc:
            self._fail(capture_id, str(exc), permanent=False)
        except Exception as exc:  # noqa: BLE001 - disco pieno, DB, bug: si riprova con backoff
            log.exception("elaborazione di %s interrotta da un errore locale", capture_id)
            self._fail(capture_id, f"errore locale: {type(exc).__name__}", permanent=False)

    def _stage(self, capture_id: uuid.UUID) -> str | None:
        with self.sessionmaker() as s:
            job = jobs.get_job(s, capture_id)
            return job.stage if job is not None else None

    def _transcribe(self, capture_id: uuid.UUID, configs: list[ProviderConfig],
                    language: str) -> bool:
        """True se si prosegue con l'arricchimento."""
        with self.sessionmaker() as s:
            capture = s.get(Capture, capture_id)
            if capture is None or capture.trashed_at is not None:
                return self._drop(s, capture_id)
            if "transcript" in capture.edited and capture.transcript is not None:
                jobs.advance(s, capture_id, jobs.STAGE_ENRICH, self.clock())
                s.commit()
                return True
            wav = self.archive.abs(capture.rel_path)
        with tempfile.TemporaryDirectory(prefix=TEMP_PREFIX) as tmp:
            audio = AudioSource(wav, Path(tmp))
            config, transcript = chain.transcribe(configs, self.factory, audio, language,
                                                  self._on_call)
        now = self.clock()
        with self.sessionmaker() as s:
            capture = notes.lock_capture(s, capture_id)
            if capture is None or capture.trashed_at is not None:
                return self._drop(s, capture_id)
            note = notefile.apply_transcript(
                notes.note_from_capture(capture), transcript.text, provider=config.name,
                model=transcript.model, language=language, at=now)
            record = {"schema_version": AI_SCHEMA_VERSION,
                      "transcribe": _ai_record(config.name, transcript.model, now, transcript.raw)}
            self.archive.write_text(capture.rel_path, AI_SUFFIX, _dump(record))
            notes.write_note(s, self.archive, capture, note, self.tz)
            proceed = bool(note.transcript)
            if proceed:
                jobs.advance(s, capture_id, jobs.STAGE_ENRICH, now)
            else:  # nessun parlato: niente arricchimento, resta il titolo di default
                jobs.mark_done(s, capture_id, now)
            s.commit()
            rel_path = capture.rel_path
        log.info("trascritta %s con %s", rel_path, config.name)
        return proceed

    def _enrich(self, capture_id: uuid.UUID, configs: list[ProviderConfig],
                language: str) -> None:
        with self.sessionmaker() as s:
            capture = s.get(Capture, capture_id)
            if capture is None or capture.trashed_at is not None:
                self._drop(s, capture_id)
                return
            text = capture.transcript
            if text is None:  # catalogo senza testo (es. `.md` cancellato): si ricomincia
                jobs.release(s, capture_id, self.clock(), stage=jobs.STAGE_TRANSCRIBE)
                s.commit()
                return
            if not text.strip():
                jobs.mark_done(s, capture_id, self.clock())
                s.commit()
                return
        config, enrichment = chain.enrich(configs, self.factory, text, language, self._on_call)
        now = self.clock()
        with self.sessionmaker() as s:
            capture = notes.lock_capture(s, capture_id)
            if capture is None or capture.trashed_at is not None:
                self._drop(s, capture_id)
                return
            note = notefile.apply_enrichment(
                notes.note_from_capture(capture), enrichment.title, enrichment.summary,
                enrichment.tags, provider=config.name, model=enrichment.model, at=now)
            self._write_ai_section(capture.rel_path, "enrich",
                                   _ai_record(config.name, enrichment.model, now, enrichment.raw))
            notes.write_note(s, self.archive, capture, note, self.tz)
            jobs.mark_done(s, capture_id, now)
            s.commit()
            rel_path = capture.rel_path
        log.info("elaborata %s con %s", rel_path, config.name)

    def _write_ai_section(self, rel_wav: str, section: str, record: dict) -> None:
        try:
            data = json.loads(self.archive.read_text(rel_wav, AI_SUFFIX))
        except (FileNotFoundError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data["schema_version"] = AI_SCHEMA_VERSION
        data[section] = record
        self.archive.write_text(rel_wav, AI_SUFFIX, _dump(data))

    def _drop(self, s: Session, capture_id: uuid.UUID) -> bool:
        """Nota nel cestino o eliminata: il risultato si scarta, il lavoro sparisce."""
        jobs.drop(s, capture_id)
        s.commit()
        log.info("nota %s nel cestino o eliminata: risultato scartato", capture_id)
        return False

    def _on_call(self, provider: str, audio_seconds: float) -> None:
        try:
            with self.sessionmaker() as s:
                record_usage(s, provider, month_start(self.clock(), self.tz), audio_seconds)
                s.commit()
        except SQLAlchemyError:
            log.exception("utilizzo di %s non registrato", provider)

    def _fail(self, capture_id: uuid.UUID, error: str, *, permanent: bool) -> None:
        with self.sessionmaker() as s:
            job = jobs.fail(s, capture_id, self.clock(), error, permanent=permanent)
            s.commit()
        if job is not None:
            log.warning("nota %s: %s (tentativo %d, ora %s)", capture_id, error, job.attempts,
                        job.status)

    def _release(self, capture_id: uuid.UUID) -> None:
        try:
            with self.sessionmaker() as s:
                jobs.release(s, capture_id, self.clock())
                s.commit()
        except SQLAlchemyError:
            log.exception("impossibile rimettere in coda %s: lo riprenderà il lease", capture_id)


def run_forever(worker, stop: threading.Event, interval: float = POLL_INTERVAL_S) -> None:
    log.info("worker avviato")
    try:
        while not stop.is_set():
            try:
                busy = worker.run_once()
            except SQLAlchemyError:
                log.exception("database non raggiungibile, riprovo tra poco")
                busy = False
            if not busy:
                stop.wait(interval)
    except Shutdown:
        pass
    log.info("worker fermato")


def install_signal_handlers(stop: threading.Event) -> None:
    """SIGTERM (docker stop) e SIGINT interrompono anche una chiamata HTTP in corso."""
    def handler(signum, frame):
        stop.set()
        raise Shutdown

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)
```

- [ ] **Step 3: Comando `worker`**

In `backend/secondbrain/cli.py`, import: `import logging` e `import threading` (con gli altri della libreria standard), `import httpx` prima di `from sqlalchemy.orm import Session`, e

```python
from .ai.registry import build_provider
from .settings_store import SecretBox
from .worker import LOG_FORMAT, Worker, install_signal_handlers, run_forever
```

(nei rispettivi posti in ordine alfabetico); prima di `_gen_key`:

```python
def _worker(args: argparse.Namespace) -> None:
    settings = load_settings()
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # una riga per richiesta è rumore
    box = SecretBox(settings.settings_key)
    if not box.available:
        logging.getLogger(__name__).warning(
            "SETTINGS_KEY non impostata: il worker resta fermo (vedi 'secondbrain gen-key')")
    engine = catalog.make_engine(settings.database_url)
    client = httpx.Client()
    stop = threading.Event()
    install_signal_handlers(stop)
    worker = Worker(sessionmaker=catalog.make_sessionmaker(engine),
                    archive=Archive(settings.archive_dir), tz=settings.tz_archive, box=box,
                    factory=lambda config: build_provider(config, client))
    try:
        run_forever(worker, stop)
    finally:
        client.close()
        engine.dispose()


def _add_worker_command(sub) -> None:
    sub.add_parser("worker", help="elabora le note in coda (servizio worker del compose)") \
        .set_defaults(func=_worker)
```

e `_add_worker_command` in fondo a `COMMAND_GROUPS`.

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 5: Prova a mano senza provider veri**

Run (con il Postgres di test e un archivio di prova):
```bash
DATABASE_URL=postgresql+psycopg://sb:sb@localhost:55432/sb_test ARCHIVE_DIR=/tmp/sb-archivio uv run secondbrain worker
```
Expected: log `worker avviato` e `worker fermo: SETTINGS_KEY non impostata`; `Ctrl-C` → `worker fermato` ed exit 0 senza traceback.

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/worker.py backend/secondbrain/cli.py backend/tests/ai_fakes.py backend/tests/test_worker.py
git commit -m "backend: worker dell'elaborazione AI in due fasi

Un lavoro alla volta: trascrizione con la catena dei provider, .md con il
solo testo e .ai.json con la risposta grezza, poi titolo, riassunto e tag.
Le chiamate ai provider avvengono senza tenere lock; il risultato si scrive
sotto il lock della riga rileggendo percorso, cestino e campi corretti a
mano, così né una correzione né un cestino durante l'elaborazione vanno
persi. Errori dell'audio: failed subito; provider tutti giù: backoff; SIGTERM
rimette in coda il lavoro in corso ed esce subito."
```

---

### Task 11: `rescan` legge i `.md` e rimette in coda le note senza

**Files:**
- Modify: `backend/secondbrain/rescan.py`
- Modify: `backend/secondbrain/cli.py` (riepilogo di `rescan`)
- Test: `backend/tests/test_rescan.py`

**Interfaces:**
- Consumes: `Archive.read_text`, `NOTE_SUFFIX` (Task 9); `parse_note`, `NoteError` (Task 2); `notes.EMPTY_AI_FIELDS`, `notes.note_fields` (Task 9); `refresh_search_vector` (Task 9); `jobs.is_active`, `jobs.enqueue`, `PRIORITY_LOW` (Task 8).
- Produces: `RescanReport.queued: int`; `rescan(s, archive, now)` che, per ogni registrazione ritrovata: con `.md` leggibile ricostruisce `transcript`, `title_auto`, `summary`, `tags`, `edited`, `language` e provenienza; senza `.md` azzera quei campi e, se fuori dal cestino e senza un lavoro attivo, la mette in coda con priorità bassa; con `.md` illeggibile lo segnala in `problems` e lascia il catalogo com'è. Alla fine ricalcola l'indice di ricerca di tutte. Continua a fare solo `flush`.

Le impostazioni (`settings`, `ai_providers`) e l'utilizzo non si ricostruiscono: sono configurazione, come utenti e sessioni (spec §8).

- [ ] **Step 1: Test (falliscono)**

In `backend/tests/test_rescan.py` gli import in cima diventano:

```python
from datetime import date, timedelta
import uuid
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select, text

from secondbrain import jobs, library
from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.models import Capture, Device, Job
from secondbrain.notefile import Note, render_note
from secondbrain.rescan import rescan
from secondbrain.sidecar import capture_to_sidecar
from tests.helpers import DEV, DEV2, NOW, TEST_DB, make_wav, upload
```

in `test_cli_rescan` l'ultima riga diventa:

```python
    out = capsys.readouterr().out
    assert "aggiunte 2" in out and "messe in coda da elaborare 2" in out
```

e in fondo al file:

```python
ROME = ZoneInfo("Europe/Rome")
PROCESSED = Note(transcript="Devo chiamare Marco per il preventivo", title="Chiamare Marco",
                 summary="Preventivo del tetto.", tags=("lavoro",), language="it",
                 provider="groq", transcribe_model="whisper-large-v3-turbo",
                 enrich_provider="groq", enrich_model="openai/gpt-oss-120b", processed_at=NOW,
                 edited=("tags",))


def searchable(db, capture_id, words):
    return db.scalar(text("SELECT search_vector @@ websearch_to_tsquery('italian', :q) "
                          "FROM captures WHERE id = :id"), {"q": words, "id": capture_id})


def test_reads_ai_fields_from_the_note(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.updated, report.problems) == (1, [])
    cap = first(db)
    assert (cap.transcript, cap.title_auto, cap.summary, cap.tags, cap.edited, cap.ai_provider,
            cap.processed_at) == (PROCESSED.transcript, "Chiamare Marco", "Preventivo del tetto.",
                                  ["lavoro"], ["tags"], "groq", NOW)
    assert searchable(db, cap.id, "chiamato")
    assert rescan(db, archive, NOW).updated == 0


def test_note_edited_by_hand_on_disk_is_picked_up(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    rescan(db, archive, NOW)
    db.commit()
    edited = archive.read_text(cap.rel_path, ".md").replace("title: Chiamare Marco",
                                                            "title: Telefonare a Marco")
    archive.write_text(cap.rel_path, ".md", edited)
    rescan(db, archive, NOW)
    db.commit()
    assert first(db).title_auto == "Telefonare a Marco"
    assert searchable(db, cap.id, "telefonare")


def test_rebuilt_catalog_keeps_the_ai_fields(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    db.execute(delete(Capture))
    db.commit()
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.added, report.queued) == (2, 1)  # solo quella senza .md torna in coda
    assert first(db).summary == "Preventivo del tetto."


def test_notes_without_md_are_queued_at_low_priority(archive, db):
    db.execute(delete(Job))
    db.commit()
    report = rescan(db, archive, NOW)
    db.commit()
    assert report.queued == 2
    assert {j.priority for j in db.scalars(select(Job))} == {jobs.PRIORITY_LOW}
    assert rescan(db, archive, NOW).queued == 0  # già in coda


def test_trashed_notes_are_not_queued(archive, db):
    library.trash_capture(db, archive, first(db).id, NOW)
    db.execute(delete(Job))
    db.commit()
    assert rescan(db, archive, NOW).queued == 1


def test_broken_note_is_reported_and_keeps_the_catalog(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    rescan(db, archive, NOW)
    jobs.mark_done(db, cap.id, NOW)
    db.commit()
    archive.write_text(cap.rel_path, ".md", "---\ntitle: [non chiusa\n---\n")
    report = rescan(db, archive, NOW)
    db.commit()
    assert any("nota .md non leggibile" in p for p in report.problems)
    assert (report.updated, report.queued) == (0, 0)
    assert first(db).summary == "Preventivo del tetto."


def test_deleted_note_clears_the_fields_and_requeues(archive, db):
    cap = first(db)
    archive.write_text(cap.rel_path, ".md", render_note(PROCESSED, ROME))
    rescan(db, archive, NOW)
    jobs.mark_done(db, cap.id, NOW)
    db.commit()
    archive.derived_path(cap.rel_path, ".md").unlink()
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.updated, report.queued) == (1, 1)
    assert (first(db).transcript, first(db).tags) == (None, [])
```

Run: `uv run pytest tests/test_rescan.py -q`
Expected: FAIL (i campi AI restano vuoti, `RescanReport` non ha `queued`).

- [ ] **Step 2: `rescan.py`**

`backend/secondbrain/rescan.py` (file completo: cambiano docstring, import, `RescanReport`, la nuova `_note_fields` e il ciclo finale di `rescan`):

```python
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
from .notes import EMPTY_AI_FIELDS, note_fields
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
        return dict(EMPTY_AI_FIELDS), True
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
        ai_fields, missing_note = _note_fields(archive, values["rel_path"], report)
        if ai_fields is not None:
            values.update(ai_fields)
        if missing_note and values["trashed_at"] is None:
            to_queue.append(values["id"])
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

    for capture_id in entries:
        refresh_search_vector(s, s.get(Capture, capture_id))
    for capture_id in to_queue:
        if not jobs.is_active(s, capture_id):
            jobs.enqueue(s, capture_id, now, priority=jobs.PRIORITY_LOW)
            report.queued += 1
    return report
```

- [ ] **Step 3: Riepilogo della CLI**

In `backend/secondbrain/cli.py`, in `_rescan`:

```python
    print(f"Registrazioni: aggiunte {report.added}, aggiornate {report.updated}, "
          f"rimosse dal catalogo {report.removed}, messe in coda da elaborare {report.queued}.")
```

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS (anche i test di `rescan` esistenti: una nota appena caricata ha già il suo lavoro in coda, quindi non viene rimessa in coda e non conta come aggiornata).

- [ ] **Step 5: Commit**

```bash
git add backend/secondbrain/rescan.py backend/secondbrain/cli.py backend/tests/test_rescan.py
git commit -m "backend: rescan ricostruisce i campi AI dai .md

Il .md è la verità per trascrizione, titolo automatico, riassunto e tag
anche dopo un trasloco o la perdita del database: rescan li rilegge, prende
le modifiche fatte a mano in Obsidian e ricalcola l'indice di ricerca. Una
nota senza .md torna in coda a priorità bassa; un .md illeggibile finisce
nel report senza cancellare niente dal catalogo, come i sidecar."
```

---

### Task 12: Finder — stato nella lista, tag, dettaglio con modifica inline, Rielabora

**Files:**
- Create: `backend/secondbrain/web/ai.py`
- Modify: `backend/secondbrain/catalog.py` (`list_day`, `list_estimated` precaricano il lavoro)
- Modify: `backend/secondbrain/web/templating.py` (titolo mostrato, conferma di Rielabora, costanti)
- Modify: `backend/secondbrain/web/context.py` (avviso "perché le note non avanzano")
- Modify: `backend/secondbrain/app.py` (`app.state.box`, router)
- Create: `backend/secondbrain/web/templates/_row_head.html`, `_ai_field.html`, `_field_saved.html`, `_reprocess.html`
- Modify: `backend/secondbrain/web/templates/_row.html`, `_detail.html`, `_icons.html`, `finder.html`, `trash.html`, `backend/secondbrain/web/static/style.css`
- Modify: `backend/tests/ai_fakes.py` (`processed`)
- Test: `backend/tests/test_web_ai.py`, `backend/tests/test_templating.py`

**Interfaces:**
- Consumes: `library.edit_ai_field`, `library.reprocess` (Task 8, 9); `jobs.queue_counts`, `JobRunning` (Task 8); `settings_store.SecretBox`, `is_paused`, `has_configured_provider` (Task 3); `EDITABLE_FIELDS` (Task 2); `Capture.job` (Task 1); dipendenze `get_db`, `require_login`, `require_csrf`, `base_context`, `page_context`, macro `icon(name)` (esistenti).
- Produces:
  - Route: `GET /captures/{id}/head` (intestazione della riga; `286` se la nota è sparita o nel cestino, così htmx smette di interrogare), `GET /captures/{id}/field/{field}[?edit=1]`, `POST /captures/{id}/field/{field}` (campo `value`, CSRF), `POST /captures/{id}/reprocess` (CSRF; `409` se in cestino o in corso).
  - `templating`: `ROW_POLL_S = 5`, `DETAIL_FIELDS = ("summary", "tags", "transcript")`, `FIELD_LABELS`, `display_title(capture, tz) -> str` (manuale → automatico → default), `reprocess_confirm(capture) -> str`; tutti anche come globali Jinja.
  - `context.ai_notice(db, box) -> str | None` e la chiave `ai_notice` in `page_context`; costanti `NOTICE_NO_KEY`, `NOTICE_PAUSED`, `NOTICE_NO_PROVIDER`.
  - `app.state.box: SecretBox`.
  - Icone nuove per `icon(name)`: `clock`, `spinner`, `pencil`, `refresh`, `search`, `settings`, `up`, `down` (le ultime quattro servono ai Task 13 e 14).
  - `tests.ai_fakes.processed(db, settings, capture_id, **over) -> Capture`.

Scelte UI (spec §10):
- Si ricarica ogni 5 s solo l'intestazione della riga (`<span class="head">`: titolo, icona di stato, primi 3 tag), non la riga intera: così un dettaglio aperto non si chiude. Quando il lavoro finisce, la risposta non ha più `hx-trigger` e il polling si ferma da solo.
- Salvare un campo o "Rielabora" restituisce anche l'intestazione con `hx-swap-oob`, così tag e icona nella riga si aggiornano (e il polling riparte dopo "Rielabora").
- Il dettaglio mostra i tre campi solo se c'è una trascrizione o una correzione; altrimenti "Trascrizione non ancora disponibile.". "Rielabora" è nascosto mentre il lavoro è in corso. Nel cestino i campi si leggono ma non si modificano.

- [ ] **Step 1: Helper e test (falliscono)**

In `backend/tests/ai_fakes.py`, import in cima: `from secondbrain import jobs, notes` (con `settings_store`), `from secondbrain.archive import Archive`, `from secondbrain.notefile import Note`, `from tests.helpers import NOW`; in fondo:

```python
def processed(db, settings, capture_id, **over):
    """La nota come la lascia il worker a elaborazione finita (lavoro `done`)."""
    fields = dict(transcript=DEFAULT_TEXT, title="Chiamare Marco", summary="Preventivo del tetto.",
                  tags=("lavoro", "casa"), language="it", provider="groq",
                  transcribe_model="whisper-large-v3-turbo", enrich_provider="groq",
                  enrich_model="openai/gpt-oss-120b", processed_at=NOW)
    fields.update(over)
    capture = notes.lock_capture(db, capture_id)
    notes.write_note(db, Archive(settings.archive_dir), capture, Note(**fields),
                     settings.tz_archive)
    jobs.mark_done(db, capture_id, NOW)
    db.commit()
    return capture
```

In `backend/tests/test_templating.py`: import `from secondbrain.models import Capture` e `display_title`, `reprocess_confirm` da `secondbrain.web.templating`; in fondo:

```python
def test_display_title_prefers_manual_then_auto_then_default():
    at = datetime(2026, 9, 28, 19, 15, tzinfo=UTC)
    assert display_title(Capture(title="Mio", title_auto="Auto", recorded_at=at), ROME) == "Mio"
    assert display_title(Capture(title=None, title_auto="Auto", recorded_at=at), ROME) == "Auto"
    assert display_title(Capture(title=None, title_auto=None, recorded_at=at), ROME) == (
        "Nota della sera, 21:15")


def test_reprocess_confirm_names_the_edited_fields():
    assert reprocess_confirm(Capture(edited=[])) == "Rielaborare la nota?"
    assert reprocess_confirm(Capture(edited=["tags", "summary"])) == (
        "Rielaborare la nota? I campi corretti a mano (riassunto, tag) resteranno invariati.")
```

`backend/tests/test_web_ai.py`:

```python
from sqlalchemy import update

from secondbrain import jobs
from secondbrain import settings_store as store
from secondbrain.models import Capture, Job
from tests.ai_fakes import configure_providers, processed
from tests.helpers import NOW, capture_by

CID = "cap_20260923_191530"


def htmx(ui):
    return {"X-CSRF-Token": ui.csrf, "HX-Request": "true"}


def cap_id(db):
    return capture_by(db, CID).id


def head(ui, capture_id):
    return ui.client.get(f"/captures/{capture_id}/head")


def test_queued_row_shows_the_clock_and_polls(recordings, db):
    text = head(recordings, cap_id(db)).text
    assert 'aria-label="In coda"' in text
    assert f'hx-get="/captures/{cap_id(db)}/head"' in text and 'hx-trigger="every 5s"' in text
    assert 'aria-label="In coda"' in recordings.client.get("/browse/2026/09/23").text


def test_running_and_failed_rows(recordings, db):
    running = jobs.claim(db, NOW).capture_id
    assert 'aria-label="In elaborazione"' in head(recordings, running).text
    jobs.fail(db, running, NOW, "groq: HTTP 400: audio non valido", permanent=True)
    db.commit()
    failed = head(recordings, running).text
    assert 'aria-label="Elaborazione non riuscita"' in failed and "hx-trigger" not in failed


def test_processed_row_shows_the_auto_title_and_three_tags(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id, tags=("lavoro", "casa", "telefonate", "extra"))
    text = head(recordings, capture_id).text
    assert "Chiamare Marco" in text and "Nota della sera" not in text
    assert "telefonate" in text and "extra" not in text
    assert "hx-trigger" not in text and 'aria-label="In coda"' not in text


def test_manual_title_wins(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    r = recordings.client.post(f"/captures/{capture_id}/title", data={"title": "Mio titolo"},
                               headers=htmx(recordings))
    assert "Mio titolo" in r.text and "Chiamare Marco" not in r.text


def test_head_of_a_trashed_note_stops_polling(recordings, db):
    capture_id = cap_id(db)
    recordings.client.post(f"/captures/{capture_id}/trash", headers=htmx(recordings))
    assert head(recordings, capture_id).status_code == 286


def test_detail_shows_summary_tags_transcript_and_provenance(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    text = recordings.client.get(f"/captures/{capture_id}").text
    assert "Preventivo del tetto." in text and "Devo chiamare Marco" in text
    assert 'href="/search?tag=lavoro"' in text
    assert "Elaborata con groq · whisper-large-v3-turbo · 28/09/2026 14:00:00" in text
    assert 'placeholder="Chiamare Marco"' in text
    assert f"{capture_by(db, CID).rel_path.rsplit('/', 1)[1][:-4]}.md" in text


def test_detail_before_processing(recordings, db):
    text = recordings.client.get(f"/captures/{cap_id(db)}").text
    assert "Trascrizione non ancora disponibile." in text


def test_edit_summary_inline(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    form = recordings.client.get(f"/captures/{capture_id}/field/summary?edit=1").text
    assert '<textarea name="value"' in form and "Preventivo del tetto." in form
    r = recordings.client.post(f"/captures/{capture_id}/field/summary",
                               data={"value": "Riassunto mio"}, headers=htmx(recordings))
    assert r.status_code == 200
    assert "Riassunto mio" in r.text and 'aria-label="Corretto a mano"' in r.text
    assert 'hx-swap-oob="outerHTML"' in r.text
    db.expire_all()
    assert db.get(Capture, capture_id).edited == ["summary"]


def test_edit_tags_and_transcript(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id)
    c = recordings.client
    r = c.post(f"/captures/{capture_id}/field/tags", data={"value": "Idee, #casa"},
               headers=htmx(recordings))
    assert 'href="/search?tag=idee"' in r.text and '<span class="tag">idee</span>' in r.text
    c.post(f"/captures/{capture_id}/field/transcript", data={"value": "Testo giusto"},
           headers=htmx(recordings))
    db.expire_all()
    cap = db.get(Capture, capture_id)
    assert (cap.tags, cap.transcript, cap.edited) == (["idee", "casa"], "Testo giusto",
                                                      ["transcript", "tags"])


def test_new_actions_require_csrf(recordings, db):
    capture_id = cap_id(db)
    c = recordings.client
    assert c.post(f"/captures/{capture_id}/field/summary", data={"value": "x"}).status_code == 403
    assert c.post(f"/captures/{capture_id}/reprocess").status_code == 403


def test_unknown_field_and_trashed_note(recordings, db):
    capture_id = cap_id(db)
    c = recordings.client
    assert c.get(f"/captures/{capture_id}/field/title").status_code == 404
    c.post(f"/captures/{capture_id}/trash", headers=htmx(recordings))
    r = c.post(f"/captures/{capture_id}/field/summary", data={"value": "x"}, headers=htmx(recordings))
    assert r.status_code == 409


def test_reprocess_warns_about_edited_fields_and_requeues(recordings, db, settings):
    capture_id = cap_id(db)
    processed(db, settings, capture_id, edited=("tags",))
    detail = recordings.client.get(f"/captures/{capture_id}").text
    assert ('hx-confirm="Rielaborare la nota? I campi corretti a mano (tag) resteranno '
            'invariati."') in detail
    r = recordings.client.post(f"/captures/{capture_id}/reprocess", headers=htmx(recordings))
    assert r.status_code == 200
    assert 'aria-label="In coda"' in r.text and 'hx-swap-oob="outerHTML"' in r.text
    db.expire_all()
    assert db.get(Capture, capture_id).job.status == jobs.QUEUED


def test_reprocess_while_running_is_refused(recordings, db):
    capture_id = cap_id(db)
    db.execute(update(Job).where(Job.capture_id != capture_id).values(status=jobs.DONE))
    db.commit()
    jobs.claim(db, NOW)
    assert "Rielabora" not in recordings.client.get(f"/captures/{capture_id}").text
    r = recordings.client.post(f"/captures/{capture_id}/reprocess", headers=htmx(recordings))
    assert r.status_code == 409 and "in elaborazione" in r.text


def test_failed_note_shows_the_error(recordings, db):
    capture_id = cap_id(db)
    jobs.fail(db, capture_id, NOW, "groq: HTTP 400: audio non valido", permanent=True)
    db.commit()
    text = recordings.client.get(f"/captures/{capture_id}").text
    assert "Elaborazione non riuscita: groq: HTTP 400: audio non valido" in text
    assert 'aria-label="Rielabora"' in text


def test_finder_says_why_notes_are_waiting(recordings, db, settings):
    c = recordings.client
    assert "Nessun provider AI con una chiave" in c.get("/browse").text
    configure_providers(db, store.SecretBox(settings.settings_key), NOW)
    assert "Nessun provider AI" not in c.get("/browse").text
    store.set_paused(db, True)
    db.commit()
    assert "Elaborazione AI in pausa" in c.get("/browse").text
```

Run: `uv run pytest tests/test_web_ai.py tests/test_templating.py -q`
Expected: FAIL (`ImportError: cannot import name 'display_title'`, route `404`).

- [ ] **Step 2: Catalogo, templating, contesto, app**

In `backend/secondbrain/catalog.py`: `from sqlalchemy.orm import Session, selectinload, sessionmaker`, e

```python
def list_day(s: Session, day: date, device_id: str | None = None) -> list[Capture]:
    stmt = (select(Capture).where(Capture.day == day).order_by(Capture.recorded_at)
            .options(selectinload(Capture.job)))  # lo stato di ogni riga, in una query sola
    return list(s.scalars(_active(stmt, device_id)))
```

(e `.options(selectinload(Capture.job))` in coda allo `stmt` di `list_estimated`).

`backend/secondbrain/web/templating.py` (file completo):

```python
"""Jinja: template, filtri di formato, nomi dei mesi e dei giorni, titolo di default."""
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre")
WEEKDAYS = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
WEEKDAYS_SHORT = ("lun", "mar", "mer", "gio", "ven", "sab", "dom")
KIB = 1024
MIB = 1024 * 1024

# Fasce orarie (ora locale, inizio incluso) per il titolo di default delle catture senza titolo.
MATTINO_START_HOUR = 5
POMERIGGIO_START_HOUR = 12
SERA_START_HOUR = 18
NOTTE_START_HOUR = 23

ROW_POLL_S = 5  # righe in coda o in corso: htmx le ricarica ogni tanti secondi
DETAIL_FIELDS = ("summary", "tags", "transcript")  # ordine nel dettaglio (spec AI §10)
FIELD_LABELS = {"summary": "Riassunto", "tags": "Tag", "transcript": "Trascrizione"}


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_size(size: int) -> str:
    if size < MIB:
        return f"{max(1, round(size / KIB))} KB"
    return f"{size / MIB:.1f} MB".replace(".", ",")


def weekday_label(d: date) -> str:
    """"Lunedì 28": nome del giorno maiuscolo per la prima lettera + numero del giorno."""
    return f"{WEEKDAYS[d.weekday()].capitalize()} {d.day}"


def weekday_short(d: date) -> str:
    """"lun 28": per l'albero, dove lo spazio è poco."""
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day}"


def default_title(recorded_at_utc: datetime, tz: ZoneInfo) -> str:
    """Titolo mostrato quando la cattura non ne ha uno: solo visualizzazione, mai salvato."""
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


def display_title(capture, tz: ZoneInfo) -> str:
    """Titolo mostrato: manuale, altrimenti automatico, altrimenti il default per fascia."""
    return capture.title or capture.title_auto or default_title(capture.recorded_at, tz)


def reprocess_confirm(capture) -> str:
    """Domanda prima di "Rielabora": dice quali campi corretti a mano restano invariati."""
    edited = [FIELD_LABELS[name].lower() for name in DETAIL_FIELDS if name in (capture.edited or [])]
    if not edited:
        return "Rielaborare la nota?"
    return f"Rielaborare la nota? I campi corretti a mano ({', '.join(edited)}) resteranno invariati."


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["duration"] = format_duration
templates.env.filters["size"] = format_size
templates.env.globals["MONTHS"] = MONTHS
templates.env.globals["date"] = date
templates.env.globals["weekday_label"] = weekday_label
templates.env.globals["weekday_short"] = weekday_short
templates.env.globals["default_title"] = default_title
templates.env.globals["display_title"] = display_title
templates.env.globals["reprocess_confirm"] = reprocess_confirm
templates.env.globals["DETAIL_FIELDS"] = DETAIL_FIELDS
templates.env.globals["FIELD_LABELS"] = FIELD_LABELS
templates.env.globals["ROW_POLL_S"] = ROW_POLL_S
```

`backend/secondbrain/web/context.py` (file completo):

```python
"""Contesto comune delle pagine del finder: filtro dispositivo, fuso, albero."""
from sqlalchemy.orm import Session
from starlette.requests import Request

from .. import catalog, jobs
from .. import settings_store as store
from ..models import WebSession
from ..naming import is_valid_device_id

LOCAL_FORMAT = "%d/%m/%Y %H:%M:%S"
NOTICE_NO_KEY = "Elaborazione AI ferma: manca SETTINGS_KEY nel .env."
NOTICE_PAUSED = "Elaborazione AI in pausa: le note nuove restano in coda."
NOTICE_NO_PROVIDER = "Nessun provider AI con una chiave: le note restano in coda."


def clean_device(value: str | None) -> str | None:
    return value if value and is_valid_device_id(value) else None


def base_context(request: Request, db: Session, session: WebSession,
                 device: str | None = None) -> dict:
    tz = request.app.state.settings.tz_archive
    devices = catalog.list_devices(db)
    return {
        "csrf": session.csrf_token,
        "tz": tz,
        "local": lambda dt: dt.astimezone(tz).strftime(LOCAL_FORMAT),
        "device": device,
        "devices": devices,
        "device_names": {d.id: d.name for d in devices},
        "qs": f"?device={device}" if device else "",
        "estimated_count": catalog.count_estimated(db),
    }


def ai_notice(db: Session, box: store.SecretBox) -> str | None:
    """Perché le note in coda non avanzano, se c'è un motivo (spec AI §11)."""
    if not jobs.queue_counts(db)[jobs.QUEUED]:
        return None
    if not box.available:
        return NOTICE_NO_KEY
    if store.is_paused(db):
        return NOTICE_PAUSED
    if not store.has_configured_provider(db):
        return NOTICE_NO_PROVIDER
    return None


def page_context(request: Request, db: Session, session: WebSession, device: str | None = None,
                 year: int | None = None, month: int | None = None) -> dict:
    return base_context(request, db, session, device) | {
        "ai_notice": ai_notice(db, request.app.state.box),
        "year": year,
        "month": month,
        "tree": {
            "years": catalog.year_counts(db, device),
            "months": catalog.month_counts(db, year, device) if year else [],
            "days": catalog.day_counts(db, year, month, device) if year and month else [],
        },
    }
```

In `backend/secondbrain/app.py`: import `from .settings_store import SecretBox` e `from .web import ai as web_ai`; in `create_app`, dopo `app.state.sessionmaker = …`: `app.state.box = SecretBox(settings.settings_key)`; dopo `app.include_router(web_actions.router)`: `app.include_router(web_ai.router)`.

- [ ] **Step 3: Route**

`backend/secondbrain/web/ai.py`:

```python
"""Campi AI nel finder: stato della riga, modifica inline, "Rielabora" (spec AI §10)."""
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy.orm import Session

from .. import catalog, jobs, library
from ..models import Capture, WebSession
from ..notefile import EDITABLE_FIELDS
from .context import base_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

HTMX_STOP_POLLING = 286  # con questo codice htmx smette di ricaricare l'elemento

router = APIRouter()


def _field(field: str) -> str:
    if field not in EDITABLE_FIELDS:
        raise HTTPException(404, "campo sconosciuto")
    return field


def _capture(db: Session, capture_id: uuid.UUID) -> Capture:
    capture = catalog.get_capture(db, capture_id)
    if capture is None:
        raise HTTPException(404, "registrazione sconosciuta")
    return capture


@router.get("/captures/{capture_id}/head")
def row_head(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)):
    capture = catalog.get_capture(db, capture_id)
    if capture is None or capture.trashed_at is not None:
        return Response(status_code=HTMX_STOP_POLLING)
    return templates.TemplateResponse(request, "_row_head.html",
                                      base_context(request, db, session) | {"c": capture})


@router.get("/captures/{capture_id}/field/{field}")
def field_view(request: Request, capture_id: uuid.UUID, field: str, edit: bool = False,
               db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    return templates.TemplateResponse(request, "_ai_field.html", base_context(request, db, session) | {
        "c": capture, "field": _field(field), "editing": edit and capture.trashed_at is None})


@router.post("/captures/{capture_id}/field/{field}")
def field_save(request: Request, capture_id: uuid.UUID, field: str, value: str = Form(""),
               db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    field = _field(field)
    state = request.app.state
    try:
        capture = library.edit_ai_field(db, state.archive, state.settings.tz_archive, capture_id,
                                        field, value)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return templates.TemplateResponse(request, "_field_saved.html", base_context(request, db, session) | {
        "c": capture, "field": field, "editing": False, "oob": True})


@router.post("/captures/{capture_id}/reprocess")
def reprocess_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                    session: WebSession = Depends(require_csrf)):
    try:
        library.reprocess(db, capture_id, request.app.state.clock())
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    except jobs.JobRunning:
        return PlainTextResponse("la nota è in elaborazione proprio ora", status_code=409)
    db.expire_all()
    capture = _capture(db, capture_id)
    files = request.app.state.archive.related_files(capture.rel_path)
    return templates.TemplateResponse(request, "_reprocess.html", base_context(request, db, session) | {
        "c": capture, "files": files, "oob": True})
```

- [ ] **Step 4: Template e stile**

`backend/secondbrain/web/templates/_row_head.html`:

```html
{% from "_icons.html" import icon %}
{% set job = c.job %}
{% set pending = job is not none and job.status in ("queued", "running") %}
<span class="head" id="head-{{ c.id }}"{% if pending %} hx-get="/captures/{{ c.id }}/head" hx-trigger="every {{ ROW_POLL_S }}s" hx-swap="outerHTML"{% endif %}{% if oob %} hx-swap-oob="outerHTML"{% endif %}>
  <span class="title">{{ display_title(c, tz) }}</span>
  {% if job and job.status == "queued" %}<span class="status" role="img" aria-label="In coda" title="In coda">{{ icon("clock") }}</span>
  {% elif job and job.status == "running" %}<span class="status spin" role="img" aria-label="In elaborazione" title="In elaborazione">{{ icon("spinner") }}</span>
  {% elif job and job.status == "failed" %}<span class="status failed" role="img" aria-label="Elaborazione non riuscita" title="Elaborazione non riuscita">{{ icon("fix") }}</span>{% endif %}
  {% for t in (c.tags or [])[:3] %}<span class="tag">{{ t }}</span>{% endfor %}
</span>
```

`backend/secondbrain/web/templates/_row.html` (il titolo passa nell'intestazione):

```html
<div class="row" id="row-{{ c.id }}">
  <button type="button" class="row-main" hx-get="/captures/{{ c.id }}" hx-target="#detail-{{ c.id }}">
    <span class="time">{% if show_day %}{{ c.recorded_at.astimezone(tz).strftime('%d/%m/%Y') }} {% endif %}{{ c.recorded_at.astimezone(tz).strftime('%H:%M:%S') }}</span>
    {% include "_row_head.html" %}
    {% if c.date_estimated %}<span class="badge">data stimata</span>{% endif %}
    <span class="meta">{{ device_names.get(c.device_id, c.device_id) }} · {{ c.duration_s|duration }} · {{ c.size_bytes|size }}</span>
  </button>
  <div class="detail" id="detail-{{ c.id }}"></div>
</div>
```

`backend/secondbrain/web/templates/_ai_field.html`:

```html
{% from "_icons.html" import icon %}
{% set label = FIELD_LABELS[field] %}
<section class="ai-field" id="field-{{ field }}-{{ c.id }}">
  <h3>{{ label }}{% if field in (c.edited or []) %} <span class="edited" role="img" aria-label="Corretto a mano" title="Corretto a mano">✎</span>{% endif %}
  {% if not c.trashed_at and not editing %}<button type="button" class="pencil" hx-get="/captures/{{ c.id }}/field/{{ field }}?edit=1" hx-target="#field-{{ field }}-{{ c.id }}" hx-swap="outerHTML" aria-label="Modifica {{ label|lower }}" title="Modifica {{ label|lower }}">{{ icon("pencil") }}</button>{% endif %}</h3>
  {% if editing %}
  <form hx-post="/captures/{{ c.id }}/field/{{ field }}" hx-target="#field-{{ field }}-{{ c.id }}" hx-swap="outerHTML">
    {% if field == "transcript" %}<textarea name="value" rows="8" aria-label="{{ label }}">{{ c.transcript or '' }}</textarea>
    {% elif field == "summary" %}<textarea name="value" rows="3" maxlength="500" aria-label="{{ label }}">{{ c.summary or '' }}</textarea>
    {% else %}<input name="value" value="{{ (c.tags or [])|join(', ') }}" placeholder="lavoro, casa" aria-label="Tag separati da virgole">{% endif %}
    <button type="submit" aria-label="Salva" title="Salva">{{ icon("save") }}<span class="label">Salva</span></button>
    <button type="button" hx-get="/captures/{{ c.id }}/field/{{ field }}" hx-target="#field-{{ field }}-{{ c.id }}" hx-swap="outerHTML" aria-label="Annulla" title="Annulla">{{ icon("close") }}<span class="label">Annulla</span></button>
  </form>
  {% elif field == "tags" %}
  {% if c.tags %}<p class="tags">{% for t in c.tags %}<a class="tag" href="/search?tag={{ t|urlencode }}">{{ t }}</a>{% endfor %}</p>{% else %}<p class="empty">—</p>{% endif %}
  {% elif field == "summary" %}<p>{{ c.summary or '—' }}</p>
  {% else %}<p class="transcript">{{ c.transcript or '—' }}</p>{% endif %}
</section>
```

`backend/secondbrain/web/templates/_field_saved.html`:

```html
{% include "_ai_field.html" %}
{% include "_row_head.html" %}
```

`backend/secondbrain/web/templates/_reprocess.html`:

```html
{% include "_detail.html" %}
{% include "_row_head.html" %}
```

`backend/secondbrain/web/templates/_detail.html` (file completo):

```html
{% from "_icons.html" import icon %}
{% set job = c.job %}
<audio controls preload="none" src="/captures/{{ c.id }}/audio"></audio>
{% if job and job.status == "failed" %}<p class="notice" role="alert">{{ icon("fix") }}<span>Elaborazione non riuscita: {{ job.last_error or "errore sconosciuto" }}</span></p>{% endif %}
{% if c.transcript is not none or c.edited %}
{% for field in DETAIL_FIELDS %}{% include "_ai_field.html" %}{% endfor %}
{% if c.processed_at %}<p class="provenance">Elaborata con {{ c.ai_provider or '—' }} · {{ c.ai_transcribe_model or '—' }}{% if c.ai_enrich_provider and c.ai_enrich_provider != c.ai_provider %} · testo con {{ c.ai_enrich_provider }}{% endif %} · {{ local(c.processed_at) }}</p>{% endif %}
{% else %}<p class="empty">Trascrizione non ancora disponibile.</p>{% endif %}
<dl>
  <dt>Registrata</dt><dd>{{ local(c.recorded_at) }}{% if c.date_estimated %} <span class="badge">stimata: ora di ricezione</span>{% endif %}</dd>
  <dt>Ricevuta</dt><dd>{{ local(c.received_at) }}</dd>
  <dt>Dispositivo</dt><dd>{{ device_names.get(c.device_id, c.device_id) }} <span class="mono">{{ c.device_id }}</span></dd>
  <dt>Firmware</dt><dd>{{ c.firmware_version or '—' }}</dd>
  <dt>Alimentazione</dt><dd>{% if c.power_source == 'usb' %}USB{% elif c.power_source == 'battery' %}batteria{% if c.battery_pct is not none %} {{ c.battery_pct }} %{% endif %}{% else %}—{% endif %}{% if c.battery_v %} · {{ '%.2f'|format(c.battery_v) }} V{% endif %}</dd>
  <dt>ID cattura</dt><dd class="mono">{{ c.capture_id }}</dd>
  <dt>SHA-256</dt><dd class="mono">{{ c.sha256 }}</dd>
  <dt>File</dt><dd><ul class="files">{% for name in files %}<li><a href="/captures/{{ c.id }}/files/{{ name }}" aria-label="Scarica {{ name }}" title="Scarica {{ name }}">{{ icon("download") }}{{ name }}</a></li>{% endfor %}</ul></dd>
</dl>
<div class="actions">
{% if not c.trashed_at %}
  <form hx-post="/captures/{{ c.id }}/title" hx-target="#row-{{ c.id }}" hx-swap="outerHTML">
    <input name="title" value="{{ c.title or '' }}" placeholder="{{ c.title_auto or default_title(c.recorded_at, tz) }}" maxlength="200" aria-label="Titolo">
    <button type="submit" aria-label="Salva titolo" title="Salva titolo">{{ icon("save") }}<span class="label">Salva titolo</span></button>
  </form>
  <form hx-post="/captures/{{ c.id }}/recorded-at">
    <input type="datetime-local" name="recorded_at" step="1" required aria-label="Data e ora" value="{{ c.recorded_at.astimezone(tz).strftime('%Y-%m-%dT%H:%M:%S') }}">
    <button type="submit" aria-label="Correggi data/ora" title="Correggi data/ora">{{ icon("calendar") }}<span class="label">Correggi data/ora</span></button>
  </form>
  {% if not (job and job.status == "running") %}<button type="button" hx-post="/captures/{{ c.id }}/reprocess" hx-target="#detail-{{ c.id }}" hx-confirm="{{ reprocess_confirm(c) }}" aria-label="Rielabora" title="Rielabora">{{ icon("refresh") }}<span class="label">Rielabora</span></button>{% endif %}
  <button type="button" hx-post="/captures/{{ c.id }}/trash" hx-target="#row-{{ c.id }}" hx-swap="outerHTML" hx-confirm="Spostare la registrazione nel cestino?" aria-label="Cestino" title="Cestino">{{ icon("trash") }}<span class="label">Cestino</span></button>
{% endif %}
  <button type="button" onclick="this.closest('.detail').innerHTML=''" aria-label="Chiudi" title="Chiudi">{{ icon("close") }}<span class="label">Chiudi</span></button>
</div>
```

`backend/secondbrain/web/templates/_icons.html` (file completo, otto icone nuove prima di `endif`):

```html
{# Icone a linea, disegnate a mano (niente CDN, niente icon font): 24x24, stroke corrente. #}
{% macro icon(name) -%}
{% if name == "archive" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v11a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8"/><line x1="10" y1="12" x2="14" y2="12"/></svg>
{%- elif name == "fix" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M12 3 21 20 3 20Z"/><line x1="12" y1="9" x2="12" y2="14"/><line x1="12" y1="17.2" x2="12" y2="17.2"/></svg>
{%- elif name == "trash" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><line x1="4" y1="7" x2="20" y2="7"/><path d="M6 7l1 13a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-13"/><path d="M9 7V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v3"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>
{%- elif name == "devices" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><rect x="6" y="6" width="12" height="12" rx="2"/><rect x="10" y="10" width="4" height="4"/><line x1="12" y1="1.5" x2="12" y2="6"/><line x1="12" y1="18" x2="12" y2="22.5"/><line x1="1.5" y1="12" x2="6" y2="12"/><line x1="18" y1="12" x2="22.5" y2="12"/></svg>
{%- elif name == "logout" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M15 4h4a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-4"/><line x1="3" y1="12" x2="14" y2="12"/><polyline points="10 7 14 12 10 17"/></svg>
{%- elif name == "save" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M5 4h11l3 3v13a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/><rect x="8" y="4" width="7" height="5"/><rect x="7" y="14" width="10" height="6"/></svg>
{%- elif name == "calendar" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><rect x="3" y="5" width="18" height="16" rx="2"/><line x1="3" y1="10" x2="21" y2="10"/><line x1="8" y1="3" x2="8" y2="7"/><line x1="16" y1="3" x2="16" y2="7"/></svg>
{%- elif name == "restore" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M4 4v6h6"/><path d="M5 13a7 7 0 1 0 2-5.5L4 10"/></svg>
{%- elif name == "delete" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><circle cx="12" cy="12" r="9"/><line x1="9" y1="9" x2="15" y2="15"/><line x1="15" y1="9" x2="9" y2="15"/></svg>
{%- elif name == "close" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/></svg>
{%- elif name == "download" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M12 3v12"/><polyline points="7 11 12 16 17 11"/><line x1="4" y1="20" x2="20" y2="20"/></svg>
{%- elif name == "listen" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><polygon points="6 4 20 12 6 20 6 4"/></svg>
{%- elif name == "clock" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15 14"/></svg>
{%- elif name == "spinner" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M12 3a9 9 0 1 0 9 9"/></svg>
{%- elif name == "pencil" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M4 20h4L19 9l-4-4L4 16v4Z"/><line x1="13.5" y1="6.5" x2="17.5" y2="10.5"/></svg>
{%- elif name == "refresh" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><path d="M20 4v6h-6"/><path d="M19 13a7 7 0 1 1-2-6.5L20 10"/></svg>
{%- elif name == "search" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><circle cx="11" cy="11" r="7"/><line x1="16" y1="16" x2="21" y2="21"/></svg>
{%- elif name == "settings" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9 7 7M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/></svg>
{%- elif name == "up" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><polyline points="6 15 12 9 18 15"/></svg>
{%- elif name == "down" -%}
<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" class="icon"><polyline points="6 9 12 15 18 9"/></svg>
{%- endif -%}
{%- endmacro %}
```

In `backend/secondbrain/web/templates/finder.html`, come prima riga dentro `<main>`:

```html
    {% if ai_notice %}<p class="notice" role="status">{{ icon("fix") }}<span>{{ ai_notice }}</span> <a href="/settings">Impostazioni</a></p>{% endif %}
```

In `backend/secondbrain/web/templates/trash.html` la cella del titolo diventa `<td>{{ display_title(c, tz) }}</td>`.

`backend/secondbrain/web/static/style.css` (file completo: variabili `--tag-bg` e `--mark-bg`, `textarea` come gli input, regole nuove prima della media query):

```css
:root { --bg: #fafaf8; --fg: #1d1d1b; --muted: #6b6b66; --line: #e2e2dc; --accent: #2f5d8a; --warn: #9a5b00; --warn-bg: #fff3dc; --tag-bg: #e8eef5; --mark-bg: #fde68a; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #ececea; --muted: #a0a09a; --line: #33332f; --accent: #8db8e4; --warn: #f0b35a; --warn-bg: #3a2a10; --tag-bg: #26303b; --mark-bg: #5c4a12; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
a { color: var(--accent); text-decoration: none; }
button, input, select, textarea { font: inherit; color: inherit; }
button { cursor: pointer; background: none; border: 1px solid var(--line); border-radius: 6px; padding: .3rem .7rem; }
input, select, textarea { border: 1px solid var(--line); border-radius: 6px; padding: .3rem .5rem; background: var(--bg); }
h1 { font-size: 1.2rem; }
.login { max-width: 22rem; margin: 15vh auto; padding: 0 1rem; }
.login input { width: 100%; margin: .5rem 0; }
.error { color: var(--warn); }
#flash { position: fixed; top: .8rem; left: 50%; transform: translateX(-50%); z-index: 100;
  max-width: min(90vw, 32rem); background: var(--warn-bg); border: 1px solid var(--warn);
  border-radius: 6px; padding: .5rem .8rem; cursor: pointer; overflow-wrap: anywhere; }
.empty { color: var(--muted); }
.count { color: var(--muted); font-size: .85em; }
.mono { font-family: ui-monospace, monospace; font-size: .85em; }
.badge { background: var(--warn-bg); color: var(--warn); border-radius: 4px; padding: 0 .4rem; font-size: .85em; }
.icon { width: 1.1em; height: 1.1em; vertical-align: -0.15em; }
.top { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: center; padding: .6rem 1rem; border-bottom: 1px solid var(--line); }
.top .brand { font-weight: 600; color: var(--fg); margin-right: auto; }
.top nav { display: flex; flex-wrap: wrap; gap: .8rem; align-items: center; }
.top .brand, .top nav a, .top nav button, .actions a, .actions button { display: inline-flex; align-items: center; gap: .3rem; }
.top form { margin: 0; }
.layout { display: flex; min-height: calc(100vh - 3rem); }
.tree { width: 14rem; flex-shrink: 0; border-right: 1px solid var(--line); padding: .8rem; }
.tree ul { list-style: none; margin: 0; padding-left: .8rem; }
.tree > ul { padding-left: 0; }
.tree li { margin: .15rem 0; }
.tree .current { font-weight: 600; }
.tree-toggle { display: none; cursor: pointer; }
main { flex: 1; min-width: 0; padding: 1rem; }
.crumbs { color: var(--muted); margin-bottom: 1rem; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(9rem, 1fr)); gap: .6rem; }
.grid a { display: block; border: 1px solid var(--line); border-radius: 8px; padding: .8rem; }
.grid .label { display: block; font-weight: 600; color: var(--fg); }
.row { border-bottom: 1px solid var(--line); }
.row-main { display: flex; flex-wrap: wrap; gap: .3rem .8rem; width: 100%; text-align: left; border: 0; border-radius: 0; padding: .6rem 0; }
.row .time { font-variant-numeric: tabular-nums; }
.row .title { font-weight: 500; }
.row .meta { color: var(--muted); margin-left: auto; }
.detail:empty { display: none; }
.detail { padding: .4rem 0 1rem; }
.detail audio { width: 100%; }
.detail dl { display: grid; grid-template-columns: max-content 1fr; gap: .2rem 1rem; }
.detail dt { color: var(--muted); }
.detail dd { margin: 0; overflow-wrap: anywhere; }
.files { margin: 0; padding-left: 1rem; }
.actions { display: flex; flex-wrap: wrap; gap: .5rem; margin: .6rem 0; }
.actions form { display: flex; flex-wrap: wrap; gap: .3rem; margin: 0; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; }
td, th { text-align: left; padding: .4rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top; }
.head { display: inline-flex; flex-wrap: wrap; gap: .3rem; align-items: center; }
.tag { display: inline-block; background: var(--tag-bg); border-radius: 4px; padding: 0 .4rem; font-size: .85em; margin-right: .25rem; }
.status { display: inline-flex; color: var(--muted); }
.status.failed { color: var(--warn); }
.spin .icon { animation: spin 1.2s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spin .icon { animation: none; } }
.notice { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; background: var(--warn-bg); color: var(--warn); border-radius: 6px; padding: .4rem .7rem; }
.ai-field h3 { display: flex; align-items: center; gap: .4rem; font-size: .95rem; margin: .8rem 0 .2rem; color: var(--muted); }
.ai-field p { margin: .2rem 0; }
.ai-field form { display: flex; flex-wrap: wrap; gap: .3rem; }
.ai-field textarea, .ai-field input[name=value] { width: 100%; }
.pencil { padding: .1rem .35rem; }
.edited { color: var(--accent); }
.transcript { white-space: pre-wrap; }
.provenance { color: var(--muted); font-size: .85em; }
@media (max-width: 700px) {
  .layout { display: block; }
  .tree { width: auto; border-right: 0; border-bottom: 1px solid var(--line); }
  .tree-toggle { display: inline-block; }
  .tree > ul { display: none; }
  #tree-toggle:checked ~ ul { display: block; }
  .row .meta { margin-left: 0; width: 100%; }
  .top .brand .label, .top nav .label, .actions .label { display: none; }
}
```

- [ ] **Step 5: Test verdi e controllo a vista**

Run: `uv run pytest -q`
Expected: tutti PASS.

Controllo a vista (server locale sul Postgres di test, dopo `uv run pytest` che lo lascia migrato):
```bash
DATABASE_URL=postgresql+psycopg://sb:sb@localhost:55432/sb_test ARCHIVE_DIR=/tmp/sb-archivio ALLOW_UNAUTHENTICATED_LAN=true uv run uvicorn --factory secondbrain.app:create_app_from_env --port 8001
```
Password con `secondbrain set-password` (stesse variabili), un upload con il `curl` del README; nella lista l'orologio accanto al titolo e l'avviso "Nessun provider AI…"; nel dettaglio "Trascrizione non ancora disponibile."; da telefono (larghezza < 700 px) righe e dettaglio leggibili.

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/web/ai.py backend/secondbrain/catalog.py backend/secondbrain/web/templating.py backend/secondbrain/web/context.py backend/secondbrain/app.py backend/secondbrain/web/templates/ backend/secondbrain/web/static/style.css backend/tests/ai_fakes.py backend/tests/test_web_ai.py backend/tests/test_templating.py
git commit -m "backend: campi AI nel finder, correzione inline e Rielabora

Nella lista il titolo automatico, i primi tag e un'icona di stato (in coda,
in corso, fallita) che si aggiorna da sola finché l'elaborazione non finisce,
senza chiudere un dettaglio aperto. Nel dettaglio riassunto, tag cliccabili
e trascrizione si correggono sul posto e restano segnati come corretti a
mano; Rielabora avvisa quali campi non cambieranno. Se le note restano in
coda il finder dice perché: manca la SETTINGS_KEY, pausa o nessun provider."
```

---

### Task 13: Ricerca — `search()`, pagina `/search`, campo nell'intestazione, apertura della nota

**Files:**
- Modify: `backend/secondbrain/search.py` (`search`, `highlight`, `SearchHit`)
- Create: `backend/secondbrain/web/search.py`, `backend/secondbrain/web/templates/search.html`
- Modify: `backend/secondbrain/web/templating.py` (`MONTHS_SHORT`, `day_month_label`)
- Modify: `backend/secondbrain/web/browse.py` (`?open=<id>` sul giorno), `backend/secondbrain/web/templates/_row.html`
- Modify: `backend/secondbrain/web/templates/finder.html` (campo di ricerca, voce Ricerca), `backend/secondbrain/web/static/style.css`, `backend/secondbrain/app.py`
- Test: `backend/tests/test_search.py`

**Interfaces:**
- Consumes: `refresh_search_vector` e l'indice GIN (Task 1, 9); `text_search_config` (Task 2); `normalize_tags` (Task 2); `settings_store.get_language` (Task 3); `display_title`, icona `search` (Task 12); `tests.ai_fakes.processed` (Task 12).
- Produces:
  - `search`: `SEARCH_LIMIT = 50`, `MAX_QUERY_LEN = 200`, `MARK_START`, `MARK_END`, `SearchHit(capture: Capture, excerpt: Markup)`, `highlight(raw) -> Markup`, `search(s, q, tag, language, limit=SEARCH_LIMIT) -> list[SearchHit]`.
  - `templating.day_month_label(date) -> str` ("Martedì 29 set"), anche globale Jinja.
  - Route `GET /search?q=&tag=`; `GET /browse/AAAA/MM/GG?open=<id>` apre subito il dettaglio di quella nota.

Scelte (spec §10):
- `websearch_to_tsquery` nella configurazione della lingua impostata; filtro tag con `tags @> ARRAY[tag]` (usa l'indice GIN); cestino escluso; ordine per `ts_rank` poi data più recente. Il tag dell'URL si normalizza come quelli salvati (`#Casa` → `casa`).
- Estratto con `ts_headline` su riassunto + trascrizione; i segnaposto di evidenziazione sono caratteri Unicode privati, sostituiti con `<mark>` solo **dopo** l'escape HTML: il testo di una nota non può iniettare markup.
- Mobile: il campo nell'intestazione è nascosto dietro l'icona lente (checkbox + label, senza JavaScript, come l'albero).
- Limite accettato dalla spec: niente normalizzazione degli accenti.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_search.py`:

```python
from datetime import date

import pytest
from markupsafe import Markup

from secondbrain import library, search
from secondbrain.archive import Archive
from secondbrain.web.templating import day_month_label
from tests.ai_fakes import processed
from tests.helpers import NOW, capture_by

EVENING = "cap_20260923_191530"   # 23/09 21:15:30
MORNING = "cap_20260923_080000"   # 23/09 10:00:00
AUGUST = "cap_20260810_070000"    # 10/08 09:00:00


@pytest.fixture
def notes(recordings, db, settings):
    ids = {cid: capture_by(db, cid).id for cid in (EVENING, MORNING, AUGUST)}
    processed(db, settings, ids[EVENING], title="Chiamare Marco", summary="Preventivo.",
              tags=("casa", "lavoro"), transcript="Ho chiamato Marco per il preventivo del tetto.")
    processed(db, settings, ids[MORNING], title="Riparare il tetto", summary="Lista della spesa.",
              tags=("casa",), transcript="Pane e latte.")
    processed(db, settings, ids[AUGUST], title="Idea", summary="Un'idea per il lavoro.",
              tags=("lavori",), transcript="Scrivere la documentazione <b>subito</b> & bene.")
    return ids


def found(db, q, tag=None):
    return [hit.capture.id for hit in search.search(db, q, tag, "it")]


def test_stemming_finds_other_forms_of_the_word(notes, db):
    assert found(db, "chiamare") == [notes[EVENING]]


def test_title_weighs_more_than_transcript(notes, db):
    # a parità di parole vincerebbe la più recente (sera); il titolo pesa di più
    assert found(db, "tetto") == [notes[MORNING], notes[EVENING]]


def test_trash_is_excluded(notes, db, settings):
    library.trash_capture(db, Archive(settings.archive_dir), notes[EVENING], NOW)
    assert found(db, "tetto") == [notes[MORNING]]


def test_tag_filter_is_exact_and_combines_with_words(notes, db):
    assert found(db, "", "lavoro") == [notes[EVENING]]
    assert found(db, "", "casa") == [notes[EVENING], notes[MORNING]]  # più recente prima
    assert found(db, "spesa", "casa") == [notes[MORNING]]
    assert found(db, "spesa", "lavoro") == []


def test_nothing_to_search(notes, db):
    assert found(db, "   ") == []
    assert found(db, "il e la") == []  # solo parole vuote


def test_excerpt_highlights_the_words_and_escapes_the_rest(notes, db):
    [hit] = search.search(db, "documentazione", None, "it")
    assert "<mark>documentazione</mark>" in hit.excerpt
    assert "<b>" not in hit.excerpt and "&amp;" in hit.excerpt


def test_highlight_escapes_before_marking():
    raw = f"a <script>x</script> {search.MARK_START}parola{search.MARK_END}"
    assert search.highlight(raw) == Markup(
        "a &lt;script&gt;x&lt;/script&gt; <mark>parola</mark>")


def test_day_month_label():
    assert day_month_label(date(2026, 9, 29)) == "Martedì 29 set"


def test_search_page(notes, recordings, db):
    text = recordings.client.get("/search?q=chiamare").text
    capture_id = notes[EVENING]
    assert "Mercoledì 23 set" in text and "Chiamare Marco" in text
    assert "<mark>chiamato</mark>" in text
    assert f'href="/browse/2026/09/23?open={capture_id}#row-{capture_id}"' in text
    assert 'value="chiamare"' in text


def test_search_page_by_tag(notes, recordings):
    text = recordings.client.get("/search?tag=%23Casa").text
    assert "Chiamare Marco" in text and "Riparare il tetto" in text and "Idea" not in text
    assert 'name="tag" value="casa"' in text
    assert "Nessuna nota trovata." in recordings.client.get("/search?q=astronave").text


def test_search_requires_login(client):
    r = client.get("/search?q=x", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")


def test_header_has_search_and_menu_entry(recordings):
    text = recordings.client.get("/browse").text
    assert 'action="/search"' in text and 'aria-label="Cerca nelle note"' in text
    assert 'aria-label="Ricerca"' in text and 'class="search-open"' in text


def test_day_page_opens_the_requested_note(notes, recordings):
    capture_id = notes[EVENING]
    text = recordings.client.get(f"/browse/2026/09/23?open={capture_id}").text
    assert (f'id="detail-{capture_id}" hx-get="/captures/{capture_id}" hx-trigger="load"') in text
    assert 'hx-trigger="load"' not in recordings.client.get("/browse/2026/09/23").text
```

Run: `uv run pytest tests/test_search.py -q`
Expected: FAIL (`AttributeError: module 'secondbrain.search' has no attribute 'search'`).

- [ ] **Step 2: `search.py`**

`backend/secondbrain/search.py` (file completo):

```python
"""Ricerca full-text sulle note (spec §10).

`search_vector` si ricalcola dal codice a ogni scrittura dei campi con un'unica
espressione: peso A il titolo mostrato (manuale o automatico), B tag e riassunto,
C la trascrizione, nella configurazione di testo della lingua della nota.
"""
from dataclasses import dataclass

from markupsafe import Markup, escape
from sqlalchemy import cast, func, literal, select, text
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.orm import Session, selectinload

from .languages import text_search_config
from .models import Capture

SEARCH_LIMIT = 50
MAX_QUERY_LEN = 200
EXCERPT_CHARS = 240  # estratto senza parole da evidenziare (ricerca solo per tag)
EXCERPT_SEPARATOR = " — "
# Segnaposto di ts_headline: caratteri Unicode privati, che non compaiono nel testo delle
# note; l'estratto si fa l'escape HTML e solo dopo diventano <mark>.
MARK_START = "\ue000"
MARK_END = "\ue001"
HEADLINE_MAX_WORDS = 35
HEADLINE_MIN_WORDS = 15
HEADLINE_FRAGMENTS = 2
HEADLINE_OPTIONS = (f"StartSel={MARK_START}, StopSel={MARK_END}, MaxWords={HEADLINE_MAX_WORDS}, "
                    f"MinWords={HEADLINE_MIN_WORDS}, MaxFragments={HEADLINE_FRAGMENTS}")

_REFRESH = text("""
UPDATE captures SET search_vector =
       setweight(to_tsvector(CAST(:cfg AS regconfig), coalesce(title, title_auto, '')), 'A')
    || setweight(to_tsvector(CAST(:cfg AS regconfig),
                             array_to_string(tags, ' ') || ' ' || coalesce(summary, '')), 'B')
    || setweight(to_tsvector(CAST(:cfg AS regconfig), coalesce(transcript, '')), 'C')
WHERE id = :id
""")


def refresh_search_vector(s: Session, capture: Capture) -> None:
    """Da chiamare dopo ogni modifica di titolo o campi AI, prima del commit."""
    s.flush()
    s.execute(_REFRESH, {"cfg": text_search_config(capture.language), "id": capture.id})


@dataclass(frozen=True)
class SearchHit:
    capture: Capture
    excerpt: Markup


def highlight(raw: str | None) -> Markup:
    """Estratto sicuro: prima l'escape di tutto il testo, poi i segnaposto diventano <mark>."""
    safe = str(escape(raw or ""))
    return Markup(safe.replace(MARK_START, "<mark>").replace(MARK_END, "</mark>"))


def search(s: Session, q: str, tag: str | None, language: str,
           limit: int = SEARCH_LIMIT) -> list[SearchHit]:
    """Note fuori dal cestino per parole (`websearch_to_tsquery`) e/o tag esatto.

    Ordine: pertinenza, poi data più recente; senza parole, solo data più recente.
    """
    q = q.strip()[:MAX_QUERY_LEN]
    if not q and not tag:
        return []
    cfg = cast(literal(text_search_config(language)), REGCONFIG)
    body = func.concat_ws(EXCERPT_SEPARATOR, Capture.summary, Capture.transcript)
    stmt = (select(Capture).where(Capture.trashed_at.is_(None))
            .options(selectinload(Capture.job)))
    if tag:
        stmt = stmt.where(Capture.tags.contains([tag]))
    if q:
        query = func.websearch_to_tsquery(cfg, q)
        stmt = (stmt.add_columns(func.ts_headline(cfg, body, query, HEADLINE_OPTIONS))
                .where(Capture.search_vector.bool_op("@@")(query))
                .order_by(func.ts_rank(Capture.search_vector, query).desc(),
                          Capture.recorded_at.desc()))
    else:
        stmt = (stmt.add_columns(func.left(body, EXCERPT_CHARS))
                .order_by(Capture.recorded_at.desc()))
    return [SearchHit(capture, highlight(raw)) for capture, raw in s.execute(stmt.limit(limit))]
```

- [ ] **Step 3: Pagina e route**

`backend/secondbrain/web/search.py`:

```python
"""Pagina di ricerca: `/search?q=parole&tag=lavoro` (spec AI §10)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import search as fulltext
from .. import settings_store as store
from ..models import WebSession
from ..notefile import normalize_tags
from .context import page_context
from .deps import get_db, require_login
from .templating import templates

router = APIRouter()


@router.get("/search")
def search_page(request: Request, q: str = "", tag: str = "", db: Session = Depends(get_db),
                session: WebSession = Depends(require_login)):
    q = q.strip()[:fulltext.MAX_QUERY_LEN]
    clean = normalize_tags([tag])
    the_tag = clean[0] if clean else None
    hits = fulltext.search(db, q, the_tag, store.get_language(db))
    return templates.TemplateResponse(request, "search.html", page_context(request, db, session) | {
        "crumbs": [("Ricerca", None)], "q": q, "tag": the_tag, "hits": hits})
```

`backend/secondbrain/web/templates/search.html`:

```html
{% extends "finder.html" %}
{% from "_icons.html" import icon %}
{% block main %}
<form class="search-page" method="get" action="/search" role="search">
  <input type="search" name="q" value="{{ q }}" placeholder="Cerca nelle note" aria-label="Cerca nelle note" autofocus>
  {% if tag %}<input type="hidden" name="tag" value="{{ tag }}"><span class="tag">{{ tag }} <a href="/search?q={{ q|urlencode }}" aria-label="Togli il filtro {{ tag }}" title="Togli il filtro">×</a></span>{% endif %}
  <button type="submit" aria-label="Cerca" title="Cerca">{{ icon("search") }}<span class="label">Cerca</span></button>
</form>
{% if q or tag %}
{% for h in hits %}
{% set c = h.capture %}
<a class="hit" href="/browse/{{ c.day.strftime('%Y/%m/%d') }}?open={{ c.id }}#row-{{ c.id }}">
  <span class="day">{{ day_month_label(c.day) }}</span>
  <span class="title">{{ display_title(c, tz) }}</span>
  {% for t in (c.tags or [])[:3] %}<span class="tag">{{ t }}</span>{% endfor %}
  {% if h.excerpt %}<span class="excerpt">{{ h.excerpt }}</span>{% endif %}
</a>
{% else %}<p class="empty">Nessuna nota trovata.</p>{% endfor %}
{% else %}<p class="empty">Cerca per parole (anche "tra virgolette" o -escluse) o apri un tag dal dettaglio di una nota.</p>{% endif %}
{% endblock %}
```

In `backend/secondbrain/web/templating.py`, dopo `WEEKDAYS_SHORT`:

```python
MONTHS_SHORT = ("gen", "feb", "mar", "apr", "mag", "giu", "lug", "ago", "set", "ott", "nov", "dic")
```

prima di `default_title`:

```python
def day_month_label(d: date) -> str:
    """"Lunedì 29 set": per i risultati della ricerca, che mescolano giorni diversi."""
    return f"{weekday_label(d)} {MONTHS_SHORT[d.month - 1]}"
```

e in fondo `templates.env.globals["day_month_label"] = day_month_label`.

In `backend/secondbrain/app.py`: `from .web import search as web_search` e `app.include_router(web_search.router)` dopo quello di `web_ai`.

- [ ] **Step 4: Apertura della nota dal risultato**

In `backend/secondbrain/web/browse.py`: `Query` negli import da `fastapi`; `browse_day` prende anche `open_id: uuid.UUID | None = Query(None, alias="open")` (dopo `device`) e lo passa al template:

```python
    return templates.TemplateResponse(request, "day.html", ctx | {
        "crumbs": crumbs, "day_num": day, "captures": catalog.list_day(db, the_day, device),
        "open_id": open_id})  # nota da aprire subito (clic su un risultato della ricerca)
```

In `backend/secondbrain/web/templates/_row.html` il contenitore del dettaglio diventa:

```html
  <div class="detail" id="detail-{{ c.id }}"{% if open_id == c.id %} hx-get="/captures/{{ c.id }}" hx-trigger="load"{% endif %}></div>
```

- [ ] **Step 5: Intestazione e stile**

In `backend/secondbrain/web/templates/finder.html`, prima di `{% set fix_label = … %}`:

```html
  <input type="checkbox" id="search-toggle" hidden>
  <label for="search-toggle" class="search-open" aria-label="Apri la ricerca" title="Cerca">{{ icon("search") }}</label>
  <form class="search" method="get" action="/search" role="search">
    <input type="search" name="q" value="{{ q or '' }}" placeholder="Cerca nelle note" aria-label="Cerca nelle note">
    <button type="submit" aria-label="Cerca" title="Cerca">{{ icon("search") }}</button>
  </form>
```

e come prima voce di `<nav>`:

```html
    <a href="/search" aria-label="Ricerca" title="Ricerca">{{ icon("search") }}<span class="label">Ricerca</span></a>
```

In `backend/secondbrain/web/static/style.css`, prima di `@media (max-width: 700px) {`:

```css
.top form.search { display: flex; gap: .3rem; }
.search-open { display: none; cursor: pointer; }
.search-page { display: flex; flex-wrap: wrap; gap: .4rem; align-items: center; margin-bottom: 1rem; }
.search-page input[type=search] { flex: 1; min-width: 12rem; }
.hit { display: block; border-bottom: 1px solid var(--line); padding: .6rem 0; color: var(--fg); }
.hit .day { color: var(--muted); margin-right: .6rem; }
.hit .title { font-weight: 500; margin-right: .4rem; }
.hit .excerpt { display: block; color: var(--muted); margin-top: .2rem; }
mark { background: var(--mark-bg); color: inherit; border-radius: 2px; }
```

e dentro la media query, dopo la riga che nasconde le etichette:

```css
  .search-open { display: inline-flex; }
  .top form.search { display: none; width: 100%; }
  #search-toggle:checked ~ form.search { display: flex; }
  .top form.search input { flex: 1; }
```

- [ ] **Step 6: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/secondbrain/search.py backend/secondbrain/web/search.py backend/secondbrain/web/templates/search.html backend/secondbrain/web/templating.py backend/secondbrain/web/browse.py backend/secondbrain/web/templates/_row.html backend/secondbrain/web/templates/finder.html backend/secondbrain/web/static/style.css backend/secondbrain/app.py backend/tests/test_search.py
git commit -m "backend: ricerca full-text sulle note e filtro per tag

Parole cercate con lo stemming della lingua delle note (chiamare trova
chiamato), titolo più pesante di tag e riassunto e questi della trascrizione,
cestino escluso. Ogni risultato mostra il giorno, i tag e un estratto con le
parole evidenziate, costruito facendo l'escape del testo prima di inserire i
<mark>; il clic apre la nota direttamente nella sua giornata. Campo di
ricerca nell'intestazione, dietro la lente sul telefono."
```

---

### Task 14: Pagina impostazioni `/settings`

**Files:**
- Create: `backend/secondbrain/web/settings.py`, `backend/secondbrain/web/templates/settings.html`
- Modify: `backend/secondbrain/app.py` (`app.state.http`, router, import in ordine)
- Modify: `backend/secondbrain/web/templates/finder.html` (voce Impostazioni), `backend/secondbrain/web/static/style.css`
- Test: `backend/tests/test_web_settings.py`

**Interfaces:**
- Consumes: `settings_store.*` (Task 3); `build_provider`, `ProviderConfig`, `PROVIDER_SPECS`, `ProviderSpec` (Task 3, 6); `CheckResult` (Task 4); `usage_for_month`, `month_start` (Task 7); `jobs.queue_counts`, `count_backfill`, `enqueue_backfill` (Task 8); `LANGUAGES` (Task 2); icone `up`, `down`, `refresh`, `settings`, `delete`, `save` (Task 12 ed esistenti); `tests.ai_fakes.Recorder` (Task 5).
- Produces:
  - Route (tutte con login; le `POST` con CSRF): `GET /settings[?queued=N]`, `POST /settings/general` (`language`, `active`), `POST /settings/providers/{name}` (`api_key`, `transcribe_model`, `text_model`, `enabled`; `409` se si prova a salvare una chiave senza `SETTINGS_KEY`), `POST /settings/providers/{name}/key/delete`, `POST /settings/providers/{name}/move` (`direction` = `up`/`down`), `POST /settings/providers/{name}/test` (frammento htmx `<span class="test-result ok|ko">`), `POST /settings/backfill` (→ `303 /settings?queued=N`). Provider sconosciuto → `404`.
  - `app.state.http: httpx.Client` (chiuso alla fine del lifespan), usato solo da "Prova".

Scelte (spec §8):
- Form HTML normali con redirect `303` come la pagina Dispositivi; solo "Prova" è htmx, perché aspetta la risposta del provider (timeout 15 s).
- Il campo della chiave è `type="password"`, sempre vuoto, e disabilitato senza `SETTINGS_KEY`; la pagina mostra solo `key_status` (maschera, "nessuna chiave" o "chiave illeggibile: reinseriscila"). Nemmeno l'errore di "Prova" può contenere la chiave: passa da `redact` negli adattatori (Review Focus 3).
- Minuti del mese corrente con la virgola decimale, come le dimensioni nel finder; niente euro (spec §13).

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_web_settings.py` (la chiave mai nell'HTML, nemmeno dopo un errore di "Prova", è il punto 3 della Review Focus):

```python
from dataclasses import replace
from datetime import date

import httpx
import pytest
from sqlalchemy import delete, select

from secondbrain import jobs
from secondbrain import settings_store as store
from secondbrain.models import AiProvider, Job, WebSession
from secondbrain.usage import record_usage
from secondbrain.web.auth import set_password
from tests.ai_fakes import Recorder
from tests.helpers import NOW, PASSWORD

KEY = "gsk_test_secret_0123456789abcdef"
MODELS = {"data": [{"id": "whisper-large-v3-turbo"}, {"id": "openai/gpt-oss-120b"}]}


def form(ui, **data):
    return {"csrf": ui.csrf, **data}


def htmx(ui):
    return {"X-CSRF-Token": ui.csrf, "HX-Request": "true"}


def save(client, csrf, name="groq", **over):
    data = {"csrf": csrf, "api_key": KEY, "transcribe_model": "whisper-large-v3-turbo",
            "text_model": "openai/gpt-oss-120b", "enabled": "1"} | over
    return client.post(f"/settings/providers/{name}", data=data, follow_redirects=False)


def mock_http(ui, recorder):
    ui.client.app.state.http.close()
    ui.client.app.state.http = httpx.Client(transport=httpx.MockTransport(recorder))


def test_page_lists_the_providers_with_their_defaults(ui):
    text = ui.client.get("/settings").text
    assert text.index("Groq") < text.index("Gemini") < text.index("OpenAI")
    for model in ("whisper-large-v3-turbo", "openai/gpt-oss-120b", "gemini-3.8-flash",
                  "gpt-4o-mini-transcribe"):
        assert f'value="{model}"' in text
    assert "principale" in text and "riserva 1" in text and "nessuna chiave" in text
    assert 'aria-label="Impostazioni"' in text


def test_saved_key_is_only_ever_shown_masked(ui, db):
    r = save(ui.client, ui.csrf)
    assert (r.status_code, r.headers["location"]) == (303, "/settings")
    text = ui.client.get("/settings").text
    assert KEY not in text and "gsk_…def" in text
    db.expire_all()
    assert KEY not in db.get(AiProvider, "groq").api_key_enc
    save(ui.client, ui.csrf, api_key="")  # campo vuoto: la chiave resta
    assert "gsk_…def" in ui.client.get("/settings").text


def test_settings_actions_require_csrf(ui):
    c = ui.client
    for url in ("/settings/general", "/settings/providers/groq", "/settings/providers/groq/test",
                "/settings/providers/groq/move", "/settings/providers/groq/key/delete",
                "/settings/backfill"):
        assert c.post(url, data={"api_key": KEY}).status_code == 403, url


def test_unknown_provider_is_404(ui):
    assert save(ui.client, ui.csrf, name="acme").status_code == 404


def test_prova_lists_the_models_with_the_saved_key(ui):
    save(ui.client, ui.csrf)
    rec = Recorder(httpx.Response(200, json=MODELS))
    mock_http(ui, rec)
    r = ui.client.post("/settings/providers/groq/test", headers=htmx(ui))
    assert 'class="test-result ok"' in r.text and "whisper-large-v3-turbo" in r.text
    assert str(rec.requests[0].url) == "https://api.groq.com/openai/v1/models"
    assert rec.requests[0].headers["authorization"] == f"Bearer {KEY}"


def test_prova_error_never_shows_the_key(ui):
    save(ui.client, ui.csrf)
    mock_http(ui, Recorder(httpx.Response(401, json={"error": {"message": f"bad key {KEY}"}})))
    r = ui.client.post("/settings/providers/groq/test", headers=htmx(ui))
    assert 'class="test-result ko"' in r.text and KEY not in r.text and "HTTP 401" in r.text


def test_prova_without_a_key(ui):
    r = ui.client.post("/settings/providers/gemini/test", headers=htmx(ui))
    assert "Nessuna chiave salvata" in r.text


def test_order_moves_up_and_down(ui, db):
    c = ui.client
    c.post("/settings/providers/gemini/move", data=form(ui, direction="up"))
    db.expire_all()
    assert [r.name for r in store.list_providers(db)] == ["gemini", "groq", "openai"]
    c.post("/settings/providers/gemini/move", data=form(ui, direction="down"))
    db.expire_all()
    assert [r.name for r in store.list_providers(db)] == ["groq", "gemini", "openai"]
    assert c.post("/settings/providers/gemini/move", data=form(ui, direction="x")).status_code == 400


def test_pause_and_language(ui, db):
    c = ui.client
    c.post("/settings/general", data=form(ui, language="en"))  # casella non spuntata = pausa
    db.expire_all()
    assert (store.is_paused(db), store.get_language(db)) == (True, "en")
    c.post("/settings/general", data=form(ui, language="it", active="1"))
    db.expire_all()
    assert (store.is_paused(db), store.get_language(db)) == (False, "it")
    assert c.post("/settings/general", data=form(ui, language="xx")).status_code == 400


def test_remove_key(ui, db):
    save(ui.client, ui.csrf)
    ui.client.post("/settings/providers/groq/key/delete", data=form(ui))
    db.expire_all()
    assert db.get(AiProvider, "groq").api_key_enc is None


def test_backfill_button(recordings, db):
    db.execute(delete(Job))
    db.commit()
    c = recordings.client
    assert "Elabora le note senza trascrizione (3)" in c.get("/settings").text
    r = c.post("/settings/backfill", data=form(recordings), follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/settings?queued=3")
    assert "3 note messe in coda." in c.get(r.headers["location"]).text
    assert {j.priority for j in db.scalars(select(Job))} == {jobs.PRIORITY_LOW}


def test_usage_and_queue(recordings, db):
    record_usage(db, "groq", date(2026, 9, 1), 90.0)
    db.commit()
    text = recordings.client.get("/settings").text
    assert "Questo mese: 1,5 min di audio · 1 chiamate" in text
    assert "3 in coda · 0 in corso · 0 fallite" in text


@pytest.fixture
def ui_without_key(make_client, settings, db):
    client = make_client(replace(settings, settings_key=None, allow_unauthenticated_lan=True))
    set_password(db, PASSWORD, NOW)
    db.commit()
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    return client, db.scalars(select(WebSession)).one().csrf_token


def test_without_settings_key(ui_without_key):
    client, csrf = ui_without_key
    text = client.get("/settings").text
    assert "SETTINGS_KEY" in text and "secondbrain gen-key" in text
    assert save(client, csrf).status_code == 409
    assert save(client, csrf, api_key="", transcribe_model="altro").status_code == 303
```

Run: `uv run pytest tests/test_web_settings.py -q`
Expected: FAIL (`/settings` risponde `404`).

- [ ] **Step 2: Route**

`backend/secondbrain/web/settings.py`:

```python
"""Pagina delle impostazioni dell'elaborazione AI (spec AI §8).

La chiave API è in sola scrittura: il campo è sempre vuoto, dopo il salvataggio si vede
solo mascherata, e non arriva mai al browser in nessuna risposta.
"""
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from markupsafe import escape
from sqlalchemy.orm import Session

from .. import jobs
from .. import settings_store as store
from ..ai.base import CheckResult
from ..ai.registry import PROVIDER_SPECS, ProviderConfig, ProviderSpec, build_provider
from ..languages import LANGUAGES
from ..models import AiProvider, WebSession
from ..usage import month_start, usage_for_month
from .context import page_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

SECONDS_PER_MINUTE = 60
MINUTES_DECIMALS = 1
NO_SETTINGS_KEY = "SETTINGS_KEY non impostata: le chiavi API non si possono salvare"

router = APIRouter()


@dataclass(frozen=True)
class ProviderView:
    row: AiProvider
    spec: ProviderSpec
    key_status: str
    minutes: float
    calls: int


def _spec(name: str) -> ProviderSpec:
    spec = PROVIDER_SPECS.get(name)
    if spec is None:
        raise HTTPException(404, "provider sconosciuto")
    return spec


def _back() -> RedirectResponse:
    return RedirectResponse("/settings", status_code=303)


@router.get("/settings")
def settings_page(request: Request, queued: int | None = None, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_login)):
    state = request.app.state
    now = state.clock()
    rows = store.ensure_providers(db, now)
    db.commit()
    usage = usage_for_month(db, month_start(now, state.settings.tz_archive))
    views = []
    for row in rows:
        used = usage.get(row.name)
        views.append(ProviderView(
            row=row, spec=PROVIDER_SPECS[row.name], key_status=store.key_status(row, state.box),
            minutes=round(used.audio_seconds / SECONDS_PER_MINUTE, MINUTES_DECIMALS) if used else 0.0,
            calls=used.calls if used else 0))
    return templates.TemplateResponse(request, "settings.html", page_context(request, db, session) | {
        "crumbs": [("Impostazioni", None)], "providers": views, "box_available": state.box.available,
        "paused": store.is_paused(db), "language": store.get_language(db), "languages": LANGUAGES,
        "counts": jobs.queue_counts(db), "backfill_count": jobs.count_backfill(db),
        "queued_now": queued})


@router.post("/settings/general")
def save_general(language: str = Form(""), active: str | None = Form(None),
                 db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        store.set_language(db, language)
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    store.set_paused(db, active is None)
    db.commit()
    return _back()


@router.post("/settings/providers/{name}")
def save_provider(request: Request, name: str, api_key: str = Form(""),
                  transcribe_model: str = Form(""), text_model: str = Form(""),
                  enabled: str | None = Form(None), db: Session = Depends(get_db),
                  session: WebSession = Depends(require_csrf)):
    _spec(name)
    now = request.app.state.clock()
    store.ensure_providers(db, now)
    try:
        store.save_provider(db, request.app.state.box, name, api_key=api_key,
                            transcribe_model=transcribe_model, text_model=text_model,
                            enabled=enabled is not None, now=now)
    except store.NoSettingsKey:
        return PlainTextResponse(NO_SETTINGS_KEY, status_code=409)
    db.commit()
    return _back()


@router.post("/settings/providers/{name}/key/delete")
def delete_key(request: Request, name: str, db: Session = Depends(get_db),
               session: WebSession = Depends(require_csrf)):
    _spec(name)
    now = request.app.state.clock()
    store.ensure_providers(db, now)
    store.clear_api_key(db, name, now)
    db.commit()
    return _back()


@router.post("/settings/providers/{name}/move")
def move(request: Request, name: str, direction: str = Form(""), db: Session = Depends(get_db),
         session: WebSession = Depends(require_csrf)):
    _spec(name)
    if direction not in (store.MOVE_UP, store.MOVE_DOWN):
        return PlainTextResponse("direzione non valida", status_code=400)
    store.move_provider(db, name, direction, request.app.state.clock())
    db.commit()
    return _back()


@router.post("/settings/providers/{name}/test")
def test_provider(request: Request, name: str, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_csrf)) -> HTMLResponse:
    """"Prova": elenco dei modelli con la chiave salvata; mostra l'esito, mai la chiave."""
    _spec(name)
    box = request.app.state.box
    row = db.get(AiProvider, name)
    key = box.decrypt(row.api_key_enc) if row is not None and row.api_key_enc else None
    if not box.available:
        result = CheckResult(False, NO_SETTINGS_KEY)
    elif row is None or row.api_key_enc is None:
        result = CheckResult(False, "Nessuna chiave salvata")
    elif key is None:
        result = CheckResult(False, store.UNREADABLE_KEY)
    else:
        config = ProviderConfig(name=name, transcribe_model=row.transcribe_model,
                                text_model=row.text_model, api_key=key)
        result = build_provider(config, request.app.state.http).check()
    css = "ok" if result.ok else "ko"
    return HTMLResponse(f'<span class="test-result {css}" role="status">{escape(result.message)}</span>')


@router.post("/settings/backfill")
def backfill(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_csrf)):
    n = jobs.enqueue_backfill(db, request.app.state.clock())
    db.commit()
    return RedirectResponse(f"/settings?queued={n}", status_code=303)
```

- [ ] **Step 3: Template, menu e stile**

`backend/secondbrain/web/templates/settings.html`:

```html
{% extends "finder.html" %}
{% from "_icons.html" import icon %}
{% block main %}
{% if not box_available %}<p class="notice" role="alert">{{ icon("fix") }}<span><code>SETTINGS_KEY</code> non impostata: le chiavi API non si possono salvare e l'elaborazione è ferma. Generala con <code>secondbrain gen-key</code>, mettila nel <code>.env</code> e riavvia app e worker.</span></p>{% endif %}
{% if queued_now is not none %}<p class="notice" role="status">{{ queued_now }} note messe in coda.</p>{% endif %}

<section class="card">
  <h2>Elaborazione</h2>
  <form method="post" action="/settings/general" class="settings-form">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label class="check"><input type="checkbox" name="active" value="1"{% if not paused %} checked{% endif %}> Elaborazione automatica attiva</label>
    <label>Lingua delle note
      <select name="language">{% for code, lang in languages.items() %}<option value="{{ code }}"{% if code == language %} selected{% endif %}>{{ lang.name }}</option>{% endfor %}</select>
    </label>
    <div class="actions"><button type="submit" aria-label="Salva" title="Salva">{{ icon("save") }}<span class="label">Salva</span></button></div>
  </form>
  <p>Coda: {{ counts.queued }} in coda · {{ counts.running }} in corso · {{ counts.failed }} fallite</p>
  <form method="post" action="/settings/backfill" class="actions">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <button type="submit"{% if not backfill_count %} disabled{% endif %}>{{ icon("refresh") }}<span>Elabora le note senza trascrizione ({{ backfill_count }})</span></button>
  </form>
</section>

{% for p in providers %}
{% set name = p.row.name %}
<section class="card" id="provider-{{ name }}">
  <h2>{{ p.spec.label }} <span class="count">{% if loop.first %}principale{% else %}riserva {{ loop.index - 1 }}{% endif %}{% if not p.row.enabled %} · disabilitato{% endif %}</span></h2>
  <div class="actions">
    <form method="post" action="/settings/providers/{{ name }}/move"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="direction" value="up"><button type="submit"{% if loop.first %} disabled{% endif %} aria-label="Sposta {{ p.spec.label }} su" title="Sposta su">{{ icon("up") }}</button></form>
    <form method="post" action="/settings/providers/{{ name }}/move"><input type="hidden" name="csrf" value="{{ csrf }}"><input type="hidden" name="direction" value="down"><button type="submit"{% if loop.last %} disabled{% endif %} aria-label="Sposta {{ p.spec.label }} giù" title="Sposta giù">{{ icon("down") }}</button></form>
  </div>
  <p>Chiave: <span class="mono">{{ p.key_status }}</span></p>
  <form method="post" action="/settings/providers/{{ name }}" class="settings-form">
    <input type="hidden" name="csrf" value="{{ csrf }}">
    <label>Nuova chiave API
      <input type="password" name="api_key" autocomplete="off" placeholder="{{ 'lascia vuoto per non cambiarla' if p.row.api_key_enc else 'incolla la chiave' }}"{% if not box_available %} disabled{% endif %}>
    </label>
    <label>Modello di trascrizione <input name="transcribe_model" value="{{ p.row.transcribe_model }}" maxlength="100"></label>
    <label>Modello di testo <input name="text_model" value="{{ p.row.text_model }}" maxlength="100" placeholder="da scegliere"></label>
    <label class="check"><input type="checkbox" name="enabled" value="1"{% if p.row.enabled %} checked{% endif %}> Abilitato</label>
    <div class="actions"><button type="submit" aria-label="Salva {{ p.spec.label }}" title="Salva">{{ icon("save") }}<span class="label">Salva</span></button></div>
  </form>
  <div class="actions">
    <button type="button" hx-post="/settings/providers/{{ name }}/test" hx-target="#test-{{ name }}" hx-swap="innerHTML">Prova</button>
    <span id="test-{{ name }}"></span>
    {% if p.row.api_key_enc %}<form method="post" action="/settings/providers/{{ name }}/key/delete" onsubmit="return confirm('Rimuovere la chiave di {{ p.spec.label }}?')"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit" aria-label="Rimuovi la chiave di {{ p.spec.label }}" title="Rimuovi chiave">{{ icon("delete") }}<span class="label">Rimuovi chiave</span></button></form>{% endif %}
  </div>
  <p class="count">Questo mese: {{ ('%.1f'|format(p.minutes)).replace('.', ',') }} min di audio · {{ p.calls }} chiamate</p>
</section>
{% endfor %}
{% endblock %}
```

In `backend/secondbrain/web/templates/finder.html`, dopo la voce Dispositivi:

```html
    <a href="/settings" aria-label="Impostazioni" title="Impostazioni">{{ icon("settings") }}<span class="label">Impostazioni</span></a>
```

In `backend/secondbrain/web/static/style.css`, prima di `@media (max-width: 700px) {`:

```css
.card { border: 1px solid var(--line); border-radius: 8px; padding: .8rem 1rem; margin-bottom: 1rem; }
.card h2 { font-size: 1.05rem; margin: 0 0 .5rem; }
.settings-form { display: grid; gap: .5rem; max-width: 32rem; }
.settings-form label { display: grid; gap: .2rem; }
.settings-form label.check { display: flex; gap: .4rem; align-items: center; }
.test-result.ok { color: var(--accent); }
.test-result.ko { color: var(--warn); }
```

- [ ] **Step 4: App**

`backend/secondbrain/app.py` (file completo: rispetto a prima dei Task 12-14 ci sono `SecretBox` e il client HTTP nello stato, i tre router nuovi e la chiusura del client nel lifespan):

```python
"""Applicazione FastAPI: stato condiviso, router, ciclo di vita (spec §5)."""
import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import datetime

import httpx
from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import ingest, library, ota
from .archive import Archive
from .catalog import make_engine, make_sessionmaker
from .clock import utcnow
from .config import Settings, load_settings
from .httputil import public_host
from .settings_store import SecretBox
from .web import actions as web_actions
from .web import ai as web_ai
from .web import browse as web_browse
from .web import login as web_login
from .web import search as web_search
from .web import settings as web_settings
from .web.auth import CsrfError, NotAuthenticated
from .web.templating import STATIC_DIR

log = logging.getLogger(__name__)

DEVICE_PATHS = ("/captures", "/firmware/")
PURGE_INTERVAL_S = 24 * 3600


def _purge_once(app: FastAPI) -> int:
    with app.state.sessionmaker() as s:
        return library.purge_trash(s, app.state.archive, app.state.clock(),
                                   app.state.settings.trash_retention_days)


async def _purge(app: FastAPI) -> None:
    try:
        removed = await run_in_threadpool(_purge_once, app)
    except Exception:  # noqa: BLE001 - la pulizia non deve fermare il servizio
        log.exception("pulizia del cestino fallita")
        return
    if removed:
        log.info("cestino: eliminate %d registrazioni scadute", removed)


async def _purge_loop(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(PURGE_INTERVAL_S)
        await _purge(app)


def _is_device_path(path: str) -> bool:
    return path == DEVICE_PATHS[0] or path.startswith(DEVICE_PATHS[1])


def create_app(settings: Settings, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        removed = app.state.archive.clean_incoming()
        if removed:
            log.warning("rimossi %d upload incompleti da .incoming", removed)
        await _purge(app)
        task = asyncio.create_task(_purge_loop(app))
        yield
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        app.state.http.close()
        app.state.engine.dispose()

    app = FastAPI(title="secondbrain", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.archive = Archive(settings.archive_dir)
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)
    app.state.box = SecretBox(settings.settings_key)
    app.state.http = httpx.Client()  # solo per "Prova"; nei test lo si sostituisce con un MockTransport

    if settings.device_hostname:
        @app.middleware("http")
        async def device_host_guard(request: Request, call_next):
            if (public_host(request) == settings.device_hostname
                    and not _is_device_path(request.url.path)):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return await call_next(request)

    app.include_router(ingest.router)
    app.include_router(ota.router)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(web_login.router)
    app.include_router(web_browse.router)
    app.include_router(web_actions.router)
    app.include_router(web_ai.router)
    app.include_router(web_search.router)
    app.include_router(web_settings.router)

    @app.exception_handler(NotAuthenticated)
    async def login_required(request: Request, exc: NotAuthenticated) -> Response:
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, exc: CsrfError) -> Response:
        return PlainTextResponse("token CSRF mancante o non valido", status_code=403)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_settings())
```

- [ ] **Step 5: Test verdi e controllo a vista**

Run: `uv run pytest -q`
Expected: tutti PASS.

Controllo a vista con il server locale del Task 12 (Step 5), prima senza e poi con `SETTINGS_KEY=$(uv run secondbrain gen-key)`: senza chiave l'avviso e i campi chiave disabilitati; con chiave, salvare una chiave finta `gsk_prova_0123456789` → si vede `gsk_…789`; "Prova" risponde con un errore `HTTP 401` leggibile (chiave finta) senza la chiave; frecce su/giù cambiano "principale"; su telefono le schede stanno in colonna.

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/web/settings.py backend/secondbrain/web/templates/settings.html backend/secondbrain/web/templates/finder.html backend/secondbrain/web/static/style.css backend/secondbrain/app.py backend/tests/test_web_settings.py
git commit -m "backend: pagina impostazioni dell'elaborazione AI

Pausa, lingua, e per ogni provider chiave in sola scrittura (dopo il
salvataggio si vede solo mascherata), modelli precompilati coi default,
abilitazione, ordine principale-riserve e il pulsante Prova che elenca i
modelli con la chiave salvata. In più il pulsante per l'arretrato con il
conteggio, i minuti di audio del mese per provider e lo stato della coda.
Senza SETTINGS_KEY la pagina spiega come generarla e non accetta chiavi."
```

---

### Task 15: Servizio `worker` nel compose, README, verifica end-to-end sul Mac, chiusura

**Files:**
- Modify: `backend/docker-compose.yml` (servizio `worker`, ambiente condiviso, `SETTINGS_KEY`)
- Modify: `backend/.env.example` (`SETTINGS_KEY`)
- Modify: `backend/README.md` (installazione, struttura dei dati, comandi, sezione "Elaborazione AI" con la nota sulla privacy, backup)
- Modify: `docs/specs/2026-09-29-backend-elaborazione-ai-design.md` ("Stato"), `CLAUDE.md` (Stato, Storico, Prossima sessione)

**Interfaces:**
- Consumes: tutto il lavoro dei Task 1-14; `secondbrain worker` (Task 10), `secondbrain gen-key` (Task 3).
- Produces: `docker compose up -d --build` avvia `postgres`, `app` e `worker`; il worker parte quando l'app è healthy (le migrazioni le applica l'entrypoint dell'app, così due container non migrano insieme) e non ha healthcheck (quello dell'immagine interroga la porta 8000). Documentazione aggiornata.

Il deploy sulla ZimaBoard resta il Task 14 del piano precedente (`docs/plans/2026-09-28-backend-archivio.md`), ancora da fare: quando lo si esegue, il `worker` parte insieme al resto e `SETTINGS_KEY` va nel `.env` della ZimaBoard. Qui non si ripete.

- [ ] **Step 1: Compose e `.env.example`**

`backend/docker-compose.yml` (file completo: le variabili dell'app diventano un blocco condiviso con il worker, `SETTINGS_KEY` compresa):

```yaml
# Avvio: cp .env.example .env (e completarlo), poi docker compose up -d --build
# Con il tunnel Cloudflare: docker compose --profile tunnel up -d --build
name: secondbrain

x-app-environment: &app-environment
  DATABASE_URL: postgresql+psycopg://secondbrain:${POSTGRES_PASSWORD}@postgres:5432/secondbrain
  ARCHIVE_DIR: /data/archive
  FIRMWARE_DIR: /data/firmware
  TZ_ARCHIVE: ${TZ_ARCHIVE:-Europe/Rome}
  TRASH_RETENTION_DAYS: ${TRASH_RETENTION_DAYS:-30}
  ALLOW_UNAUTHENTICATED_LAN: ${ALLOW_UNAUTHENTICATED_LAN:-false}
  DEVICE_HOSTNAME: ${DEVICE_HOSTNAME:-}
  MAX_UPLOAD_BYTES: ${MAX_UPLOAD_BYTES:-33554432}
  SETTINGS_KEY: ${SETTINGS_KEY:-}

services:
  postgres:
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: secondbrain
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?imposta POSTGRES_PASSWORD in .env}
      POSTGRES_DB: secondbrain
    volumes:
      - ${DATA_DIR:-./data}/postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U secondbrain"]
      interval: 5s
      timeout: 3s
      retries: 20

  app:
    build: .
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    environment: *app-environment
    volumes:
      - ${DATA_DIR:-./data}/archive:/data/archive
      - ${DATA_DIR:-./data}/firmware:/data/firmware
    ports:
      # Solo IPv4: pubblicare anche su IPv6 farebbe passare quelle connessioni dal
      # docker-proxy, che le presenta all'app con l'IP del gateway del bridge (172.x,
      # privato) invece di quello reale del client, allargando la regola LAN-senza-token.
      - "0.0.0.0:${APP_PORT:-8000}:8000"

  worker:
    # Stessa immagine dell'app: elabora le note in coda (trascrizione, titolo, riassunto,
    # tag). Parte dopo l'app, che al suo avvio applica le migrazioni.
    build: .
    restart: unless-stopped
    entrypoint: ["secondbrain", "worker"]
    depends_on:
      app:
        condition: service_healthy
    environment: *app-environment
    volumes:
      - ${DATA_DIR:-./data}/archive:/data/archive
    healthcheck:
      disable: true  # il controllo dell'immagine interroga la porta 8000, che il worker non apre

  cloudflared:
    image: cloudflare/cloudflared:latest
    profiles: ["tunnel"]
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${TUNNEL_TOKEN:-}
    depends_on:
      - app
```

In fondo a `backend/.env.example`:

```
# Chiave che cifra nel database le chiavi API dei provider AI. Si genera con
# `docker compose exec app secondbrain gen-key`; senza, l'elaborazione AI resta ferma.
# Va tenuta insieme ai backup: persa questa, si reinseriscono le chiavi API dalla UI.
SETTINGS_KEY=
```

Run: `cd backend && POSTGRES_PASSWORD=x docker compose config --quiet && echo ok`
Expected: `ok`.

- [ ] **Step 2: README**

`backend/README.md` (file completo):

````markdown
# secondbrain — backend

## Cos'è

Servizio che riceve le registrazioni dal device e-Paper (e in futuro da altri
dispositivi), le archivia su disco in `archive/AAAA/MM/GG/`, le rende consultabili e
gestibili da una UI web tipo "finder" e serve il firmware via OTA. Un worker separato
trascrive ogni nota e ne ricava titolo, riassunto e tag con un provider AI esterno
(Groq, Gemini, OpenAI); il risultato si corregge nel finder e si trova con la ricerca.
Dettagli di design in `../docs/specs/2026-09-28-backend-archivio-design.md` e
`../docs/specs/2026-09-29-backend-elaborazione-ai-design.md`.

## Installazione

```bash
cd backend
cp .env.example .env
```

In `.env`, impostare almeno `POSTGRES_PASSWORD` (una password generata, non quella
d'esempio):

```bash
openssl rand -base64 24
```

Poi:

```bash
docker compose up -d --build
docker compose exec app secondbrain set-password
docker compose exec app secondbrain device add <mac> --name <nome>
docker compose exec app secondbrain gen-key
```

Incollare l'output di `gen-key` in `SETTINGS_KEY` nel `.env` e ricreare i container con
`docker compose up -d` (vedi "Elaborazione AI" più sotto): finché manca, il resto del
servizio funziona ma le note restano in coda senza trascrizione.

Il comando `device add` stampa il token una sola volta: salvarlo, serve al dispositivo
per autenticarsi (finché il firmware non lo supporta, vedi sotto e §11 della spec,
`ALLOW_UNAUTHENTICATED_LAN=true` in `.env` accetta upload senza token dalla sola LAN).

Aprire `http://<host-o-ip>:8000` ed entrare con la password impostata sopra.

L'immagine si costruisce sulla macchina di destinazione (`--build`): la base
`python:3.12-slim` è multi-architettura, quindi lo stesso compose funziona sul Mac di
sviluppo, sulla ZimaBoard o su un NAS UGREEN (amd64) e su una macchina arm64, senza
bisogno di un registry.

## Struttura dei dati

Tutto sotto `DATA_DIR` (default `./data`, montato in `/data/...` nel container):

```
data/
  archive/
    2026/09/28/
      091530_70041dd8263c.wav             audio della registrazione
      091530_70041dd8263c.json            sidecar: metadati della registrazione
      091530_70041dd8263c.md              nota: trascrizione, titolo, riassunto, tag (YAML, leggibile da Obsidian)
      091530_70041dd8263c.ai.json         risposte grezze dei provider AI, mai modificate
    .incoming/                            upload in corso (<uuid>.tmp), ripulita all'avvio
    .trash/2026/09/28/...                 cestino, con la stessa struttura dell'archivio
  firmware/
    epaper154/
      secondbrain-0.6.2.bin
  postgres/                               dati del database (catalogo, non serve leggerli)
```

Il nome base di una registrazione è `HHMMSS_<device-id>` (ora locale di registrazione,
id del dispositivo), con suffisso `_2`, `_3`... in caso di collisione. Ogni file
"correlato" a una registrazione si chiama `<base>.<qualcosa>`; il sidecar è esattamente
`<base>.json`. I file sono la fonte di verità: il catalogo Postgres si ricostruisce da
loro con `secondbrain rescan`.

## Comandi

Tutti come `docker compose exec app secondbrain ...`:

| Comando | Cosa fa |
|---|---|
| `device add <mac> --name <nome> [--type <tipo>]` | registra un dispositivo e genera il token (mostrato una volta sola) |
| `device token <mac>` | genera un nuovo token per un dispositivo (il precedente smette di valere) |
| `device list` | elenca i dispositivi registrati |
| `set-password [--stdin]` | imposta la password dell'interfaccia web |
| `unlock` | rimuove i tentativi di login falliti e sblocca l'accesso |
| `rescan` | ricostruisce il catalogo Postgres dall'archivio su disco |
| `firmware publish <file.bin> --version X.Y.Z [--type <tipo>]` | pubblica un binario come release corrente per l'OTA |
| `firmware rollback [--type <tipo>]` | torna alla release precedente |
| `firmware list` | elenca le release pubblicate (`*` = corrente) |
| `trash purge` | elimina definitivamente le registrazioni nel cestino scadute (oltre `TRASH_RETENTION_DAYS`) |
| `gen-key` | genera una `SETTINGS_KEY` (non serve il database) |
| `process <id>` | rimette in coda l'elaborazione AI di una registrazione (come "Rielabora") |
| `process --backfill` | mette in coda, a priorità bassa, tutte le note fuori dal cestino senza trascrizione |
| `worker` | il ciclo del worker (lo lancia il servizio `worker` del compose, non serve a mano) |

Per pubblicare un firmware, il binario va prima copiato dentro il container:

```bash
docker compose cp ../firmware/build/secondbrain_fw.bin app:/tmp/fw.bin
docker compose exec app secondbrain firmware publish /tmp/fw.bin --version X.Y.Z
```

## Contratto dei dispositivi

`POST /captures` (vedi spec §6 per il dettaglio completo):

```
POST /captures
Content-Type: audio/wav
Authorization: Bearer <token>                (assente solo se ALLOW_UNAUTHENTICATED_LAN e client in LAN)
X-Capture-Id: cap_20260928_091530
X-Capture-Ts: 2026-09-28T07:15:30Z            (assente per le "unsynced": data stimata dal server)
X-Device-Id: 70041dd8263c
X-Firmware-Version: 0.6.2
X-Battery-Pct: 95
X-Battery-Voltage: 4.15
X-Power-Source: battery                       (oppure usb)
<byte del WAV>
```

Risposte: `201` accettata (`{"id", "path", "status": "accepted"}`), `409` duplicato
(stesso `device_id` + `X-Capture-Id` + sha256), `400` header non validi, `401` token
assente o non valido, `403` `X-Device-Id` diverso dal dispositivo del token, `411`
`Content-Length` mancante, `413` oltre `MAX_UPLOAD_BYTES`, `422` WAV non valido, `5xx`
errori del server (disco pieno `507`, DB non raggiungibile `503`).

Cosa fa il device con ciascun esito: `201`/`409` → cancella la sua copia (accettata o già
presente, in entrambi i casi non serve più); qualunque altro `4xx` → il firmware attuale
sposta il file in `rejected/` (non ritenta, il problema non si risolve da solo);
`5xx`/timeout → tiene il file e ritenta al ciclo successivo. Per questo, finché il
firmware non manda il token, `ALLOW_UNAUTHENTICATED_LAN` va tenuto `true`: con `false` le
richieste del device (senza token) prendono `401`, che è un `4xx`, e il firmware
sposterebbe l'intera coda in `rejected/`.

`GET /firmware/manifest.json` (alias di `GET /firmware/epaper154/manifest.json`) e
`GET /firmware/<tipo>/<file>.bin` servono l'OTA pull con lo stesso schema
`{version, url, sha256}` già implementato nel firmware.

`ALLOW_UNAUTHENTICATED_LAN=true` accetta richieste senza `Authorization` solo se il
client ha un IP privato o di loopback e la richiesta non porta l'header
`Cf-Connecting-Ip` (che Cloudflare aggiunge sempre alle richieste che passano dal
tunnel): dalla LAN di casa senza token quindi funziona, dal tunnel il token resta
sempre obbligatorio.

Attenzione: questa regola si fida dell'IP reale del client, e ci arriva solo se Docker
pubblica la porta su IPv4 (`docker-compose.yml` usa `0.0.0.0:${APP_PORT}:8000` apposta).
Su Docker Desktop (Mac, Windows) il traffico passa comunque dalla VM interna e l'app vede
sempre l'IP del gateway del bridge Docker (un `172.x` privato): lì
`ALLOW_UNAUTHENTICATED_LAN=true` significa "chiunque raggiunga la porta", non solo la LAN.
Su Linux con la pubblicazione IPv4 l'IP del client resta quello vero. Dopo un deploy,
verificare nel log dell'app che una richiesta del device mostri il suo IP `192.168.x.x` e
non un `172.x`.

## Elaborazione AI

Il servizio `worker` (stessa immagine dell'app) prende dalla coda una nota alla volta:
la trascrive, poi chiede titolo, riassunto breve e tag, e scrive `<base>.md` e
`<base>.ai.json` accanto al WAV. Ogni nota nuova entra in coda appena arriva.

**Configurazione**

1. `SETTINGS_KEY` nel `.env` (vedi Installazione), poi `docker compose up -d`.
2. Nella UI, **Impostazioni** (ingranaggio): incollare la chiave API di almeno un
   provider, controllare i modelli precompilati e premere **Prova** (elenca i modelli
   con quella chiave: è gratuito). L'ordine delle schede è principale → riserve: se il
   principale non risponde (quota, chiave sbagliata, servizio giù) si passa al
   successivo; se l'audio stesso non va bene la nota risulta "fallita".
3. **Elabora le note senza trascrizione** mette in coda l'arretrato a priorità bassa: le
   note nuove passano comunque davanti.

Le chiavi API stanno nel database cifrate con `SETTINGS_KEY`; la pagina le mostra solo
mascherate. Se `SETTINGS_KEY` si perde, basta generarne un'altra e reinserire le chiavi
API: nient'altro va perso.

**Privacy.** Audio e testo delle note escono di casa verso il provider scelto (e verso le
riserve se il principale non risponde) e restano soggetti alla politica sui dati del
proprio account presso quel provider: controllarla prima di inserire la chiave. In
archivio resta il WAV originale; al provider va una copia FLAC temporanea (OpenAI riceve
il WAV).

**Log e stato**

```bash
docker compose logs -f worker      # "trascritta …", "elaborata …", errori già ripuliti dalle chiavi
```

Nel finder ogni riga mostra l'icona di stato (orologio in coda, rotella in corso,
triangolo fallita); una nota fallita mostra l'errore nel dettaglio e si rimette in coda
con **Rielabora** (i campi corretti a mano restano come sono). Se le note restano in
coda, il finder dice perché: manca `SETTINGS_KEY`, elaborazione in pausa o nessun
provider con una chiave. Il worker ritenta dopo 1 min, 5 min, 30 min, 2 h e 6 h; al
sesto tentativo fallito la nota resta "fallita".

## Accesso da fuori con Cloudflare Tunnel

1. Creare il tunnel in Cloudflare Zero Trust → Networks → Tunnels (tipo `cloudflared`)
   e copiare il token in `TUNNEL_TOKEN` (`.env`).
2. Configurare due "public hostname" verso `http://app:8000`: uno per la UI (quello che
   si apre dal browser) e uno per i dispositivi, da mettere in `DEVICE_HOSTNAME`
   (`.env`) — su quell'hostname rispondono solo `/captures` e `/firmware/*`.
3. Avviare con il profilo `tunnel`:

   ```bash
   docker compose --profile tunnel up -d --build
   ```

Attenzione: Bot Fight Mode (o altre protezioni WAF di Cloudflare) sull'hostname dei
dispositivi può bloccare le richieste del firmware, che non è un browser (vedi Task 14
del piano di questa fase).

## Backup e ripristino

- **Archivio** (`archive/`): è la fonte di verità. Basta uno snapshot del NAS o:

  ```bash
  rsync -a "$DATA_DIR/archive/" <destinazione>/
  ```

- **Database** (dispositivi, token, release firmware, utente, sessioni, impostazioni e
  chiavi API cifrate — configurazione, non archivio; trascrizioni, riassunti e tag stanno
  anche nei `.md` dell'archivio):

  ```bash
  docker compose exec -T postgres pg_dump -U secondbrain secondbrain > secondbrain.sql
  ```

- **`SETTINGS_KEY`** (nel `.env`): senza, le chiavi API del dump non si decifrano e vanno
  reinserite dalla UI.

**Ripristino su una macchina nuova:** copiare `archive/` e `firmware/` in `DATA_DIR`,
`docker compose up -d`, poi:

- con un dump: `docker compose exec -T postgres psql -U secondbrain secondbrain < secondbrain.sql`;
- senza dump: `docker compose exec app secondbrain rescan` ricostruisce le
  registrazioni dal disco, comprese trascrizioni, riassunti e tag dai `.md` (le note
  senza `.md` tornano in coda); dispositivi, release firmware e impostazioni AI vanno
  registrati di nuovo (`secondbrain device add`, `secondbrain firmware publish`, pagina
  Impostazioni).

## Sviluppo

```bash
uv sync
docker compose -f docker-compose.test.yml up -d   # Postgres usa-e-getta per i test, porta 55432
uv run pytest -q
```
````

- [ ] **Step 3: Avvio sul Mac**

Sul Mac di casa gira già il compose del backend (`secondbrain-app-1`, `secondbrain-postgres-1`) con i dati veri in `backend/data/`.

Run:
```bash
cd backend
uv run pytest -q                                   # tutto verde prima di toccare il servizio vero
docker compose up -d --build
docker compose ps                                  # app e postgres healthy, worker running
docker compose exec app secondbrain gen-key        # copiare l'output in SETTINGS_KEY nel .env
docker compose up -d                               # ricrea app e worker con la chiave
docker compose logs worker | tail -5
docker compose exec worker python -c "import soundfile; print(soundfile.__libsndfile_version__)"
```
Expected: `worker avviato` e `worker fermo: nessun provider abilitato con una chiave` nei log; versione di libsndfile stampata (la wheel la include, niente pacchetti di sistema); nel finder l'avviso "Nessun provider AI con una chiave" se c'è qualcosa in coda.

- [ ] **Step 4: Verifica end-to-end con chiavi vere e il device vero**

Servono una chiave Groq e una Gemini dell'autore (da inserire solo dalla pagina Impostazioni, mai in un file del repo). Il device punta già a `http://192.168.1.28:8000`. Annotare l'esito di ciascun punto:

1. **Chiavi e modelli.** Impostazioni → Groq: incollare la chiave, Salva, **Prova** → "Chiave valida; modelli disponibili: whisper-large-v3-turbo, openai/gpt-oss-120b". Stesso per Gemini (`gemini-3.8-flash`). Se un modello di default non c'è: scegliere quello giusto dalla documentazione del provider, correggerlo in `backend/secondbrain/ai/registry.py` e nell'atteso di `tests/test_settings_store.py::test_default_providers`, commit a parte con il perché.
2. **Nota nuova.** Registrare con PWR una nota di ~20 s con un contenuto riconoscibile (es. "devo chiamare Marco per il preventivo del tetto entro venerdì"). Nella lista del giorno: orologio → rotella → nessuna icona entro qualche decina di secondi, senza ricaricare la pagina; titolo sensato e tag. Nel dettaglio: riassunto, tag, trascrizione comprensibile, riga "Elaborata con groq · whisper-large-v3-turbo · …". Sul disco `data/archive/<giorno>/<base>.md` (leggibile, frontmatter come nella spec §6) e `<base>.ai.json`; entrambi tra i file scaricabili.
3. **Ordine.** Spostare Gemini su "principale", registrare un'altra nota → "Elaborata con gemini". Rimettere Groq principale.
4. **Fallback e chiavi mai nei log.** Salvare su Groq una chiave sbagliata (`gsk_sbagliata_0123456789abcdef`), registrare una nota → elaborata da Gemini; nel log del worker la riga "groq non disponibile, provo il successivo: HTTP 401: …" senza la chiave. Poi: `docker compose logs app worker | grep -c "gsk_sbagliata"` → `0`, e lo stesso con i primi 12 caratteri delle chiavi vere → `0`. Rimettere la chiave giusta.
5. **Pausa.** Togliere "Elaborazione automatica attiva", registrare una nota → resta "in coda", il finder dice "in pausa"; riattivare → elaborata entro pochi secondi.
6. **Arretrato.** Le note archiviate prima di questa fase: Impostazioni → "Elabora le note senza trascrizione (N)" → tutte elaborate; una nota registrata durante l'arretrato viene elaborata prima di quelle vecchie.
7. **Correzioni.** In una nota elaborata correggere tag e riassunto (matita, Salva: compare "✎"); **Rielabora** → la conferma nomina "riassunto, tag"; a fine elaborazione quei due campi sono invariati e il titolo automatico può cambiare. Correggere la trascrizione e Rielabora → la trascrizione resta quella corretta (nel log niente "trascritta …" per quella nota).
8. **Ricerca dal telefono** in LAN (`http://192.168.1.28:8000`): lente nell'intestazione, cercare una parola in un'altra forma (es. "chiamato" per una nota che dice "chiamare") → trovata, parola evidenziata; clic sul risultato → si apre la giornata con la nota già aperta; clic su un tag nel dettaglio → `/search?tag=…` con le note di quel tag.
9. **Cestino durante l'elaborazione.** Registrare una nota di ~60 s e cestinarla mentre ha la rotella → nel cestino, nessun `.md` in `data/archive/.trash/…`; ripristinarla → torna senza icona di stato (non rientra in coda da sola); "Rielabora" la elabora.
10. **Arresto del worker.** Durante un'elaborazione `docker compose stop worker` → si ferma in pochi secondi (log "worker fermato"); `docker compose start worker` → la nota viene completata.
11. **Rescan.** `docker compose exec app secondbrain rescan` → "aggiornate 0, … messe in coda da elaborare 0"; modificare a mano il `title:` di un `.md` e rilanciare → il nuovo titolo compare nel finder e si trova con la ricerca.
12. **Utilizzo.** Impostazioni → i minuti del mese per Groq e Gemini corrispondono, a occhio, alla durata delle note elaborate da ciascuno.

Se un punto fallisce: correzione con un test che riproduce il caso, nello stesso commit o nel successivo con il perché nel messaggio.

- [ ] **Step 5: Commit del deploy e della documentazione**

```bash
git add backend/docker-compose.yml backend/.env.example backend/README.md
git commit -m "backend: servizio worker nel compose e README dell'elaborazione AI

Il worker usa la stessa immagine e le stesse variabili dell'app, parte quando
l'app è healthy (le migrazioni restano all'app) e si ferma pulito con
docker compose stop. Il README spiega SETTINGS_KEY, la configurazione dei
provider, cosa esce di casa verso il provider scelto e come si leggono i
log. Verificato sul Mac con il device vero: <punti del Task 15 passati, con
eventuali note>."
```

(Il corpo del commit riporta l'esito reale della verifica.)

- [ ] **Step 6: Stato della spec e `CLAUDE.md`**

- `docs/specs/2026-09-29-backend-elaborazione-ai-design.md`, riga "Stato": "implementato e verificato col device vero sul Mac il <data> (branch `backend-elaborazione-ai`, piano `docs/plans/2026-09-29-backend-elaborazione-ai.md`); deploy sulla ZimaBoard con il Task 14 del piano archivio", più le deviazioni emerse e quelle già decise nel piano: interfaccia `transcribe(audio: AudioSource, …)` invece del percorso, chiave `enrich_provider` nel frontmatter quando l'arricchimento lo fa un altro provider, colonne `ai_transcribe_model`/`ai_enrich_provider`/`ai_enrich_model` in più sul catalogo, Groq principale di default, eventuali modelli di default cambiati al punto 1.
- `CLAUDE.md`: "Stato" (elaborazione AI attiva sul Mac, provider configurati, `SETTINGS_KEY` nel `.env` del Mac), "Storico" (voce della giornata con ciò che è emerso), "Prossima sessione" (deploy sulla ZimaBoard, Task 14 del piano archivio, ora con il worker), regole (test backend: ora sono <N>, comando invariato).

```bash
git add docs/specs/2026-09-29-backend-elaborazione-ai-design.md CLAUDE.md
git commit -m "docs: elaborazione AI verificata col device sul Mac

<esito dei punti del Task 15 e deviazioni emerse>. Resta il deploy sulla
ZimaBoard, con il worker e la SETTINGS_KEY nel suo .env."
```
