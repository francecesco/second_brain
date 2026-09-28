# Backend Fase 1b — Archivio delle registrazioni — Piano di implementazione

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un servizio `secondbrain` in Docker (app FastAPI + Postgres) che riceve le registrazioni dal device con il contratto esistente, le archivia su disco per `anno/mese/giorno` con un sidecar JSON, le rende navigabili e gestibili da una UI "finder" con login, serve l'OTA e si espone da fuori casa con un tunnel Cloudflare.

**Architecture:** I file su disco sono la verità (`archive/AAAA/MM/GG/<base>.wav` + `<base>.json`), Postgres è un catalogo ricostruibile con `secondbrain rescan`. Logica decisionale in moduli puri testati senza I/O (`naming`, `wav`, `sidecar`); `archive` fa solo filesystem; `catalog` solo query; `ingest`, `ota`, `library` e `web` coordinano. UI renderizzata dal server con Jinja + htmx, nessun build frontend.

**Tech Stack:** Python 3.12, uv, FastAPI (≥ 0.115.6) su Starlette (≥ 0.46: `Range` in `FileResponse` e indirizzo del client configurabile nel `TestClient`), SQLAlchemy 2 + psycopg 3, Alembic, Jinja2, htmx 2 (file statico), argon2-cffi, pytest + httpx; Postgres 16; Docker compose; cloudflared.

**Spec:** `docs/specs/2026-09-28-backend-archivio-design.md` (leggerla prima: i §x citati sotto sono i suoi).

## Global Constraints

- Tutto il codice backend sta in `backend/`. Comandi da `backend/`: `uv sync`, `uv run pytest -q`.
- I test di integrazione usano un Postgres vero: `docker compose -f docker-compose.test.yml up -d` (porta 55432, dati in tmpfs). Senza, i test che toccano il DB si fermano con un messaggio che dice come avviarlo. Niente SQLite nei test.
- Tutti i test esistenti devono restare verdi a ogni commit.
- Prima il test che fallisce, poi l'implementazione (come per il firmware).
- Nessun numero magico sparso: soglie e durate come costanti con nome in cima al modulo o in `config.py`.
- Datetime sempre *aware* in UTC dentro il codice e nel DB (`DateTime(timezone=True)`); la conversione al fuso `TZ_ARCHIVE` avviene solo per cartella, nome base e visualizzazione.
- Percorsi relativi all'archivio sempre in stile POSIX (`2026/09/28/x.wav`), risolti solo con `Archive.abs()`, che rifiuta ciò che esce dall'archivio.
- Nomi base `HHMMSS_<device-id>[_k]`: nessun punto nel nome base. Un file è "correlato" a una registrazione se si chiama `<base>.<qualcosa>`; il sidecar è esattamente `<base>.json`.
- Commit piccoli, messaggi in italiano con prefisso `backend:` (codice) o `docs:`, corpo che spiega il perché. **Nessun riferimento all'assistente** (niente `Co-Authored-By`, niente "Generated with"). Autore `francecesco <francecesco78@gmail.com>` (config locale del repo, già impostata).
- `main/secrets.h` del firmware resta fuori da git; le modifiche per la verifica end-to-end sono solo locali.
- Le checkbox di questo piano **non** si spuntano: lo stato si legge dai commit e dallo "Stato" della spec.

## Review Focus

Casi che la spec implica e che un uso reale incontrerà; ognuno ha un test nel task indicato.

1. Due registrazioni nella notte del cambio d'ora (25/10, 02:30 CEST e 02:30 CET) hanno la stessa ora locale → nomi distinti `023000_<dev>` e `023000_<dev>_2`, niente sovrascritture (Task 2, Task 7).
2. Il device ritenta una cattura che l'utente ha già messo nel cestino → `409`, non viene riarchiviata né "resuscitata" (Task 12).
3. File archiviati ma commit sul DB fallito → i file appena scritti vengono rimossi, così il ritentativo del device produce una sola copia (Task 7).
4. Nomi con `../` o percorsi codificati sulle route dei file e del firmware → `404`, mai un file fuori dall'archivio o dalla directory firmware (Task 9, Task 11).
5. Due dispositivi che registrano nello stesso secondo → due file distinti nella stessa cartella del giorno, grazie all'id del dispositivo nel nome (Task 7).

---

## File Structure

| File | Responsabilità | Task |
|---|---|---|
| `backend/pyproject.toml`, `uv.lock`, `.python-version`, `.gitignore` | progetto uv, dipendenze, script `secondbrain` | 1 |
| `backend/docker-compose.test.yml` | Postgres per i test | 1 |
| `backend/secondbrain/config.py` | `Settings`, `load_settings()` | 1 |
| `backend/secondbrain/clock.py` | `utcnow()` | 1 |
| `backend/secondbrain/naming.py` (puro) | id cattura/device, timestamp, cartella del giorno, nome base, suffissi | 2 |
| `backend/secondbrain/wav.py` (puro) | parsing header WAV, durata | 3 |
| `backend/secondbrain/models.py` | modelli SQLAlchemy | 4, 9, 10 |
| `backend/secondbrain/catalog.py` | engine, sessionmaker, query | 4, 11 |
| `backend/alembic.ini`, `backend/migrations/…` | migrazioni | 4, 9, 10 |
| `backend/secondbrain/sidecar.py` (puro) | Capture ↔ dict JSON | 5 |
| `backend/secondbrain/archive.py` | filesystem dell'archivio | 5 |
| `backend/secondbrain/devices.py` | token, autenticazione, metadati dagli header | 6 |
| `backend/secondbrain/cli.py` | comandi `secondbrain …` | 6, 8, 9, 10, 12 |
| `backend/secondbrain/httputil.py` | host e schema pubblici della richiesta | 7 |
| `backend/secondbrain/ingest.py` | `POST /captures` | 7 |
| `backend/secondbrain/app.py` | `create_app`, lifespan, middleware, handler | 7, 9, 10, 11, 12 |
| `backend/secondbrain/rescan.py` | ricostruzione del catalogo dal disco | 8 |
| `backend/secondbrain/ota.py` | release firmware, manifest, binari | 9 |
| `backend/secondbrain/web/templating.py` | Jinja, filtri, mesi | 10 |
| `backend/secondbrain/web/auth.py`, `deps.py`, `login.py` | login, sessioni, CSRF | 10 |
| `backend/secondbrain/web/context.py`, `browse.py` | navigazione finder | 11 |
| `backend/secondbrain/library.py` | titolo, correggi data, cestino, pulizia | 12 |
| `backend/secondbrain/web/actions.py` | route delle azioni, cestino, da sistemare, dispositivi | 12 |
| `backend/secondbrain/web/templates/*.html`, `static/*` | pagine e stile | 10, 11, 12 |
| `backend/Dockerfile`, `docker-compose.yml`, `docker-entrypoint.sh`, `.env.example`, `.dockerignore` | deploy | 13 |
| `backend/README.md` | installazione, CLI, tunnel, backup | 13, 14 |
| `backend/tests/…` | test | tutti |

---

### Task 1: Scaffold del progetto e configurazione

**Files:**
- Create: `backend/pyproject.toml`, `backend/.python-version`, `backend/.gitignore`, `backend/docker-compose.test.yml`
- Create: `backend/secondbrain/__init__.py`, `backend/secondbrain/config.py`, `backend/secondbrain/clock.py`
- Create: `backend/tests/__init__.py`, `backend/tests/helpers.py`, `backend/tests/test_config.py`

**Interfaces:**
- Produces: `Settings` (dataclass frozen: `database_url: str`, `archive_dir: Path`, `firmware_dir: Path`, `tz_archive: ZoneInfo`, `max_upload_bytes: int`, `trash_retention_days: int`, `allow_unauthenticated_lan: bool`, `device_hostname: str | None`), `load_settings(env: Mapping[str, str] | None = None) -> Settings`, `ConfigError(ValueError)`, `clock.utcnow() -> datetime`, `tests.helpers.TEST_DB: str`.

- [ ] **Step 1: File di progetto**

`backend/pyproject.toml`:

```toml
[project]
name = "secondbrain"
version = "0.1.0"
description = "Archivio delle registrazioni di Second Brain"
requires-python = ">=3.12"
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
]

[project.scripts]
secondbrain = "secondbrain.cli:main"

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["secondbrain"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`backend/.python-version`:

```
3.12
```

`backend/.gitignore`:

```
.venv/
__pycache__/
.pytest_cache/
.env
data/
```

`backend/docker-compose.test.yml`:

```yaml
# Postgres usa-e-getta per i test: docker compose -f docker-compose.test.yml up -d
services:
  postgres-test:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: sb
      POSTGRES_PASSWORD: sb
      POSTGRES_DB: sb_test
    ports:
      - "55432:5432"
    tmpfs:
      - /var/lib/postgresql/data
```

`backend/secondbrain/__init__.py`:

```python
"""Second Brain: archivio delle registrazioni."""
```

`backend/tests/__init__.py`: file vuoto.

`backend/tests/helpers.py`:

```python
"""Utilità condivise dai test."""
import os

TEST_DB = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://sb:sb@localhost:55432/sb_test"
)
```

Run: `cd backend && uv sync`
Expected: crea `.venv/` e `uv.lock` senza errori.

- [ ] **Step 2: Test della configurazione (fallisce)**

`backend/tests/test_config.py`:

```python
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from secondbrain.config import ConfigError, load_settings

BASE = {"DATABASE_URL": "postgresql+psycopg://u:p@h/db"}


def test_defaults():
    s = load_settings(BASE)
    assert s.database_url == BASE["DATABASE_URL"]
    assert s.archive_dir == Path("/data/archive")
    assert s.firmware_dir == Path("/data/firmware")
    assert s.tz_archive == ZoneInfo("Europe/Rome")
    assert s.max_upload_bytes == 32 * 1024 * 1024
    assert s.trash_retention_days == 30
    assert s.allow_unauthenticated_lan is False
    assert s.device_hostname is None


def test_overrides():
    s = load_settings(BASE | {
        "ARCHIVE_DIR": "/srv/a", "FIRMWARE_DIR": "/srv/f", "TZ_ARCHIVE": "UTC",
        "MAX_UPLOAD_BYTES": "2048", "TRASH_RETENTION_DAYS": "7",
        "ALLOW_UNAUTHENTICATED_LAN": "true", "DEVICE_HOSTNAME": "Ingest.Example.org",
    })
    assert s.archive_dir == Path("/srv/a")
    assert s.firmware_dir == Path("/srv/f")
    assert s.tz_archive == ZoneInfo("UTC")
    assert s.max_upload_bytes == 2048
    assert s.trash_retention_days == 7
    assert s.allow_unauthenticated_lan is True
    assert s.device_hostname == "ingest.example.org"


def test_missing_database_url():
    with pytest.raises(ConfigError, match="DATABASE_URL"):
        load_settings({})


@pytest.mark.parametrize("tz", ["Mars/Olympus", "", "../etc"])
def test_bad_timezone(tz):
    with pytest.raises(ConfigError, match="TZ_ARCHIVE"):
        load_settings(BASE | {"TZ_ARCHIVE": tz})


@pytest.mark.parametrize("raw", ["forse", "2"])
def test_bad_bool(raw):
    with pytest.raises(ConfigError, match="ALLOW_UNAUTHENTICATED_LAN"):
        load_settings(BASE | {"ALLOW_UNAUTHENTICATED_LAN": raw})


@pytest.mark.parametrize("key,raw", [
    ("MAX_UPLOAD_BYTES", "tanti"), ("MAX_UPLOAD_BYTES", "10"),
    ("TRASH_RETENTION_DAYS", "0"),
])
def test_bad_int(key, raw):
    with pytest.raises(ConfigError, match=key):
        load_settings(BASE | {key: raw})
```

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'secondbrain.config'`.

- [ ] **Step 3: Implementazione**

`backend/secondbrain/config.py`:

```python
"""Configurazione del servizio, letta solo da variabili d'ambiente (spec §5)."""
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_MAX_UPLOAD_BYTES = 32 * 1024 * 1024
MIN_MAX_UPLOAD_BYTES = 1024
DEFAULT_TRASH_RETENTION_DAYS = 30

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Variabile d'ambiente mancante o non valida."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    archive_dir: Path
    firmware_dir: Path
    tz_archive: ZoneInfo
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    trash_retention_days: int = DEFAULT_TRASH_RETENTION_DAYS
    allow_unauthenticated_lan: bool = False
    device_hostname: str | None = None


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{key}: valore booleano non valido: {raw!r}")


def _int(env: Mapping[str, str], key: str, default: int, minimum: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key}: non è un intero: {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{key}: deve essere almeno {minimum}, trovato {value}")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    url = env.get("DATABASE_URL", "").strip()
    if not url:
        raise ConfigError("DATABASE_URL mancante")
    tz_name = env.get("TZ_ARCHIVE", "Europe/Rome")
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f"TZ_ARCHIVE: fuso orario sconosciuto {tz_name!r}") from None
    return Settings(
        database_url=url,
        archive_dir=Path(env.get("ARCHIVE_DIR", "/data/archive")),
        firmware_dir=Path(env.get("FIRMWARE_DIR", "/data/firmware")),
        tz_archive=tz,
        max_upload_bytes=_int(env, "MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES,
                              MIN_MAX_UPLOAD_BYTES),
        trash_retention_days=_int(env, "TRASH_RETENTION_DAYS",
                                  DEFAULT_TRASH_RETENTION_DAYS, 1),
        allow_unauthenticated_lan=_bool(env, "ALLOW_UNAUTHENTICATED_LAN", False),
        device_hostname=env.get("DEVICE_HOSTNAME", "").strip().lower() or None,
    )
```

`backend/secondbrain/clock.py`:

```python
"""Orologio del servizio: sostituibile nei test tramite create_app(clock=...)."""
from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)
```

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "backend: scaffold del progetto secondbrain e configurazione da ambiente

Progetto uv con FastAPI, SQLAlchemy/psycopg, Alembic e Jinja; configurazione solo
da variabili d'ambiente validate all'avvio, cosi' un errore in .env ferma il servizio
invece di emergere alla prima richiesta. Postgres di test in compose con dati in tmpfs."
```

---

### Task 2: `naming` — cartelle, nomi base, timestamp (puro)

**Files:**
- Create: `backend/secondbrain/naming.py`
- Test: `backend/tests/test_naming.py`

**Interfaces:**
- Produces: `DEFAULT_DEVICE_TYPE = "epaper154"`, `is_valid_capture_id(str) -> bool`, `is_valid_device_id(str) -> bool`, `is_valid_device_type(str) -> bool`, `parse_capture_ts(header: str | None, now_utc: datetime) -> datetime | None`, `resolve_recorded_at(header: str | None, now_utc: datetime) -> tuple[datetime, bool]` (seconda voce = data stimata), `local_day(utc: datetime, tz: ZoneInfo) -> date`, `day_dir(day: date) -> str` (`"2026/09/28"`), `parse_day_dir(value: str) -> date` (`ValueError` se non valido), `base_name(utc: datetime, tz: ZoneInfo, device_id: str) -> str`, `with_suffix(base: str, k: int) -> str`, `unique_base(base: str, taken: Callable[[str], bool]) -> str`.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_naming.py`:

```python
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from secondbrain import naming

ROME = ZoneInfo("Europe/Rome")
NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)
DEV = "70041dd8263c"


@pytest.mark.parametrize("cid", [
    "cap_20260923_191530", "cap_20260923_191530_2",
    "cap_unsynced_000042", "cap_unsynced_000042_3",
])
def test_valid_capture_ids(cid):
    assert naming.is_valid_capture_id(cid)


@pytest.mark.parametrize("cid", [
    "", "cap_2026_1915", "cap_unsynced_42", "../etc/passwd",
    "cap_20260923_191530.wav", "CAP_20260923_191530", "cap_20260923_191530\n",
])
def test_invalid_capture_ids(cid):
    assert not naming.is_valid_capture_id(cid)


def test_device_ids():
    assert naming.is_valid_device_id(DEV)
    for bad in ["", "70:04:1d:d8:26:3c", "70041DD8263C", "70041dd8263", "../x"]:
        assert not naming.is_valid_device_id(bad)


def test_device_types():
    assert naming.is_valid_device_type("epaper154")
    assert naming.is_valid_device_type("mic-v2_b")
    for bad in ["", "E-paper", "../x", "a" * 33, "-lead"]:
        assert not naming.is_valid_device_type(bad)


def test_ts_valid():
    assert naming.parse_capture_ts("2026-09-23T19:15:30Z", NOW) == \
        datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)


def test_ts_up_to_24h_in_future_is_accepted():
    assert naming.parse_capture_ts("2026-09-29T12:00:00Z", NOW) is not None


@pytest.mark.parametrize("raw", [
    None, "", "garbage", "2026-09-23 19:15:30", "2026-09-23T19:15:30+02:00",
    "2000-01-01T00:00:05Z", "2023-12-31T23:59:59Z", "2026-09-29T12:00:01Z",
])
def test_ts_rejected(raw):
    assert naming.parse_capture_ts(raw, NOW) is None


def test_resolve_recorded_at():
    assert naming.resolve_recorded_at(None, NOW) == (NOW, True)
    assert naming.resolve_recorded_at("2000-01-01T00:00:05Z", NOW) == (NOW, True)
    ts = datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)
    assert naming.resolve_recorded_at("2026-09-23T19:15:30Z", NOW) == (ts, False)


def test_midnight_uses_local_day():
    utc = datetime(2026, 9, 27, 22, 30, 0, tzinfo=UTC)  # 00:30 del 28 a Roma
    assert naming.local_day(utc, ROME) == date(2026, 9, 28)
    assert naming.day_dir(naming.local_day(utc, ROME)) == "2026/09/28"
    assert naming.base_name(utc, ROME, DEV) == "003000_70041dd8263c"


def test_winter_offset():
    utc = datetime(2026, 12, 1, 8, 15, 30, tzinfo=UTC)
    assert naming.base_name(utc, ROME, DEV) == "091530_70041dd8263c"


def test_dst_fall_back_same_local_time_gets_suffix():
    first = datetime(2026, 10, 25, 0, 30, tzinfo=UTC)   # 02:30 CEST
    second = datetime(2026, 10, 25, 1, 30, tzinfo=UTC)  # 02:30 CET
    a = naming.base_name(first, ROME, DEV)
    b = naming.base_name(second, ROME, DEV)
    assert a == b == "023000_70041dd8263c"
    assert naming.unique_base(b, {a}.__contains__) == "023000_70041dd8263c_2"


def test_parse_day_dir():
    assert naming.parse_day_dir("2026/09/28") == date(2026, 9, 28)
    for bad in ["2026/9/28", "2026/13/01", "2026/02/30", "../2026/09/28", "2026-09-28"]:
        with pytest.raises(ValueError):
            naming.parse_day_dir(bad)


def test_unique_base():
    assert naming.unique_base("x", lambda b: False) == "x"
    assert naming.unique_base("x", {"x", "x_2"}.__contains__) == "x_3"
    assert naming.with_suffix("x", 1) == "x"
    assert naming.with_suffix("x", 4) == "x_4"
```

Run: `uv run pytest tests/test_naming.py -q`
Expected: FAIL, `ImportError` su `secondbrain.naming`.

- [ ] **Step 2: Implementazione**

`backend/secondbrain/naming.py`:

```python
"""Nomi e cartelle dell'archivio (spec §4, §6): funzioni pure, nessun I/O."""
import re
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

DEFAULT_DEVICE_TYPE = "epaper154"
CAPTURE_ID_RE = re.compile(r"cap_(\d{8}_\d{6}|unsynced_\d{6})(_\d+)?")
DEVICE_ID_RE = re.compile(r"[0-9a-f]{12}")
DEVICE_TYPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
DAY_DIR_RE = re.compile(r"\d{4}/\d{2}/\d{2}")
TS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
MIN_PLAUSIBLE_YEAR = 2024
MAX_FUTURE = timedelta(hours=24)


def is_valid_capture_id(value: str) -> bool:
    return CAPTURE_ID_RE.fullmatch(value) is not None


def is_valid_device_id(value: str) -> bool:
    return DEVICE_ID_RE.fullmatch(value) is not None


def is_valid_device_type(value: str) -> bool:
    return DEVICE_TYPE_RE.fullmatch(value) is not None


def parse_capture_ts(header: str | None, now_utc: datetime) -> datetime | None:
    """X-Capture-Ts se ben formato e plausibile, altrimenti None."""
    if not header:
        return None
    try:
        ts = datetime.strptime(header, TS_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None
    if ts.year < MIN_PLAUSIBLE_YEAR or ts > now_utc + MAX_FUTURE:
        return None
    return ts


def resolve_recorded_at(header: str | None, now_utc: datetime) -> tuple[datetime, bool]:
    """(istante di registrazione in UTC, data_stimata)."""
    ts = parse_capture_ts(header, now_utc)
    if ts is None:
        return now_utc, True
    return ts, False


def local_day(utc: datetime, tz: ZoneInfo) -> date:
    return utc.astimezone(tz).date()


def day_dir(day: date) -> str:
    return f"{day:%Y/%m/%d}"


def parse_day_dir(value: str) -> date:
    if DAY_DIR_RE.fullmatch(value) is None:
        raise ValueError(f"cartella del giorno non valida: {value!r}")
    return datetime.strptime(value, "%Y/%m/%d").date()


def base_name(utc: datetime, tz: ZoneInfo, device_id: str) -> str:
    return f"{utc.astimezone(tz):%H%M%S}_{device_id}"


def with_suffix(base: str, k: int) -> str:
    return base if k <= 1 else f"{base}_{k}"


def unique_base(base: str, taken: Callable[[str], bool]) -> str:
    k = 1
    while taken(with_suffix(base, k)):
        k += 1
    return with_suffix(base, k)
```

- [ ] **Step 3: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/secondbrain/naming.py backend/tests/test_naming.py
git commit -m "backend: nomi e cartelle dell'archivio come funzioni pure

Cartella e ora del nome base si calcolano nel fuso locale, cosi' una registrazione
delle 00:30 finisce nel giorno giusto; timestamp assenti o implausibili (RTC azzerato)
diventano data stimata. Il cambio d'ora di ottobre produce due volte la stessa ora
locale: il suffisso _2 evita la sovrascrittura."
```

---

### Task 3: `wav` — validazione dell'header (puro)

**Files:**
- Create: `backend/secondbrain/wav.py`
- Modify: `backend/tests/helpers.py` (aggiunge `make_wav`)
- Test: `backend/tests/test_wav.py`

**Interfaces:**
- Produces: `HEADER_PROBE_BYTES = 4096`, `WavError(ValueError)`, `WavInfo(sample_rate, channels, bits_per_sample, data_bytes)` con proprietà `duration_s: float`, `parse_wav_header(head: bytes, total_size: int) -> WavInfo`; nei test `make_wav(seconds=1.0, rate=16000, fill=b"\x01\x00", audio_format=1, extra_chunk=b"", declared_data=None) -> bytes`.

- [ ] **Step 1: Helper e test (falliscono)**

Aggiungere in fondo a `backend/tests/helpers.py`:

```python
import struct


def make_wav(seconds: float = 1.0, rate: int = 16000, fill: bytes = b"\x01\x00",
             audio_format: int = 1, extra_chunk: bytes = b"",
             declared_data: int | None = None) -> bytes:
    """WAV mono 16 bit come quelli del device; `fill` (2 byte) cambia il contenuto."""
    data = fill * int(seconds * rate)
    fmt = b"fmt " + struct.pack("<IHHIIHH", 16, audio_format, 1, rate, rate * 2, 2, 16)
    size = len(data) if declared_data is None else declared_data
    body = b"WAVE" + extra_chunk + fmt + b"data" + struct.pack("<I", size) + data
    return b"RIFF" + struct.pack("<I", len(body)) + body
```

(spostare `import struct` in cima al file, accanto a `import os`).

`backend/tests/test_wav.py`:

```python
import struct

import pytest

from secondbrain.wav import WavError, parse_wav_header
from tests.helpers import make_wav


def parse(blob: bytes):
    return parse_wav_header(blob[:4096], len(blob))


def test_device_wav():
    info = parse(make_wav(1.5))
    assert (info.sample_rate, info.channels, info.bits_per_sample) == (16000, 1, 16)
    assert info.data_bytes == 48000
    assert info.duration_s == pytest.approx(1.5)


def test_skips_unknown_chunks_with_odd_padding():
    extra = b"LIST" + struct.pack("<I", 3) + b"abc" + b"\x00"
    assert parse(make_wav(1.0, extra_chunk=extra)).duration_s == pytest.approx(1.0)


def test_truncated_file_uses_available_bytes():
    blob = make_wav(1.0)[:-3200]  # manca 0,1 s
    assert parse(blob).duration_s == pytest.approx(0.9)


def test_declared_size_larger_than_file():
    assert parse(make_wav(1.0, declared_data=0xFFFFFFFF)).duration_s == pytest.approx(1.0)


@pytest.mark.parametrize("blob,msg", [
    (b"", "RIFF"),
    (b"OggS" + b"\x00" * 60, "RIFF"),
    (make_wav(1.0, audio_format=3), "PCM"),
    (make_wav(0.0), "campione"),
    (b"RIFF\x04\x00\x00\x00WAVE", "data"),
    (b"RIFF\x0c\x00\x00\x00WAVEdata\x00\x00\x00\x00", "fmt"),
])
def test_invalid(blob, msg):
    with pytest.raises(WavError, match=msg):
        parse(blob)
```

Run: `uv run pytest tests/test_wav.py -q`
Expected: FAIL, `ImportError` su `secondbrain.wav`.

- [ ] **Step 2: Implementazione**

`backend/secondbrain/wav.py`:

```python
"""Validazione dell'header WAV ricevuto (spec §6 punto 4): funzione pura."""
import struct
from dataclasses import dataclass

HEADER_PROBE_BYTES = 4096
PCM = 1
SUPPORTED_BITS = (8, 16, 24, 32)


class WavError(ValueError):
    """Il file non è un WAV PCM utilizzabile."""


@dataclass(frozen=True)
class WavInfo:
    sample_rate: int
    channels: int
    bits_per_sample: int
    data_bytes: int

    @property
    def duration_s(self) -> float:
        bytes_per_second = self.sample_rate * self.channels * self.bits_per_sample // 8
        return self.data_bytes / bytes_per_second


def parse_wav_header(head: bytes, total_size: int) -> WavInfo:
    """`head`: i primi byte del file (fino a HEADER_PROBE_BYTES); `total_size`: byte totali."""
    if len(head) < 12 or head[0:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise WavError("non è un file RIFF/WAVE")
    fmt: tuple[int, int, int] | None = None
    pos = 12
    while pos + 8 <= len(head):
        chunk_id = head[pos:pos + 4]
        (size,) = struct.unpack_from("<I", head, pos + 4)
        body = pos + 8
        if chunk_id == b"fmt ":
            if size < 16 or body + 16 > len(head):
                raise WavError("chunk fmt troncato")
            audio_format, channels, rate, _, _, bits = struct.unpack_from("<HHIIHH", head, body)
            if audio_format != PCM:
                raise WavError(f"formato {audio_format} non PCM")
            if channels == 0 or rate == 0 or bits not in SUPPORTED_BITS:
                raise WavError("parametri del chunk fmt non validi")
            fmt = (rate, channels, bits)
        elif chunk_id == b"data":
            if fmt is None:
                raise WavError("chunk data prima del chunk fmt")
            data_bytes = min(size, max(total_size - body, 0))
            if data_bytes == 0:
                raise WavError("nessun campione audio")
            return WavInfo(fmt[0], fmt[1], fmt[2], data_bytes)
        pos = body + size + (size & 1)
    raise WavError("chunk data non trovato")
```

- [ ] **Step 3: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/secondbrain/wav.py backend/tests/helpers.py backend/tests/test_wav.py
git commit -m "backend: validazione dell'header WAV ricevuto

Un file che non e' WAV PCM va rifiutato con un 4xx, cosi' il device lo sposta in
rejected/ invece di ritentarlo all'infinito. La durata si calcola sui byte davvero
presenti: un header con dimensione dichiarata sbagliata non falsa i metadati."
```

---
### Task 4: Catalogo Postgres — modelli, migrazione iniziale, fixture di test

**Files:**
- Create: `backend/secondbrain/models.py`, `backend/secondbrain/catalog.py`
- Create: `backend/alembic.ini`, `backend/migrations/env.py`, `backend/migrations/versions/0001_devices_captures.py`
- Create: `backend/tests/conftest.py`
- Modify: `backend/tests/helpers.py` (aggiunge `make_device`, `make_capture`)
- Test: `backend/tests/test_catalog.py`

**Interfaces:**
- Consumes: `tests.helpers.TEST_DB`.
- Produces: `models.Base`, `models.Device` (campi della spec §8), `models.Capture` (campi della spec §8, compreso `day: date`); `catalog.make_engine(url) -> Engine`, `catalog.make_sessionmaker(engine) -> sessionmaker[Session]` (con `expire_on_commit=False`), `catalog.get_device(s, device_id) -> Device | None`, `catalog.get_capture(s, id: uuid.UUID) -> Capture | None`, `catalog.find_captures(s, device_id, capture_id) -> list[Capture]` (ordine: `received_at`, poi `rel_path`); fixture `engine` (sessione di test, schema migrato) e `db` (Session su DB svuotato); helper `make_device(**over) -> Device`, `make_capture(**over) -> Capture`.

- [ ] **Step 1: Modelli**

`backend/secondbrain/models.py`:

```python
"""Modelli SQLAlchemy del catalogo (spec §8)."""
import uuid
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, Double, ForeignKey, Index,
                        Integer, String, Uuid)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
```

- [ ] **Step 2: Alembic e migrazione iniziale**

`backend/alembic.ini`:

```ini
[alembic]
script_location = %(here)s/migrations
path_separator = os
# Vuoto: env.py usa DATABASE_URL. I test lo impostano con set_main_option.
sqlalchemy.url =
```

`backend/migrations/env.py`:

```python
"""Ambiente Alembic: URL da sqlalchemy.url (test) oppure da DATABASE_URL (servizio)."""
import os

from alembic import context
from sqlalchemy import create_engine, pool

url = context.config.get_main_option("sqlalchemy.url") or os.environ["DATABASE_URL"]
engine = create_engine(url, poolclass=pool.NullPool)
with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
```

`backend/migrations/versions/0001_devices_captures.py`:

```python
"""dispositivi e registrazioni

Revision ID: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "devices",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=True, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_firmware", sa.String(32), nullable=True),
        sa.Column("last_battery_pct", sa.Integer(), nullable=True),
        sa.Column("last_battery_v", sa.Double(), nullable=True),
        sa.Column("last_power_source", sa.String(16), nullable=True),
    )
    op.create_table(
        "captures",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("device_id", sa.String(32), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("capture_id", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("date_estimated", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rel_path", sa.String(255), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("duration_s", sa.Double(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("firmware_version", sa.String(32), nullable=True),
        sa.Column("battery_pct", sa.Integer(), nullable=True),
        sa.Column("battery_v", sa.Double(), nullable=True),
        sa.Column("power_source", sa.String(16), nullable=True),
        sa.Column("trashed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_captures_device_capture", "captures", ["device_id", "capture_id"])
    op.create_index("ix_captures_recorded_at", "captures", ["recorded_at"])
    op.create_index("ix_captures_day", "captures", ["day"])


def downgrade() -> None:
    op.drop_table("captures")
    op.drop_table("devices")
```

- [ ] **Step 3: Fixture e helper**

`backend/tests/conftest.py`:

```python
"""Fixture condivise: Postgres di test migrato, sessione su DB svuotato."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text

from secondbrain.catalog import make_engine, make_sessionmaker
from secondbrain.models import Base
from tests.helpers import TEST_DB

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def engine():
    eng = make_engine(TEST_DB)
    try:
        with eng.connect():
            pass
    except Exception as exc:  # noqa: BLE001 - qualunque errore di connessione
        pytest.exit(
            f"Postgres di test non raggiungibile su {TEST_DB}.\n"
            f"Avvialo con: docker compose -f docker-compose.test.yml up -d\n({exc})",
            returncode=2,
        )
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    """Sessione su un DB svuotato prima del test."""
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    with make_sessionmaker(engine)() as session:
        yield session
```

Aggiungere in fondo a `backend/tests/helpers.py` (import in cima al file):

```python
import uuid
from datetime import UTC, date, datetime

from secondbrain.models import Capture, Device

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
DEV = "70041dd8263c"
DEV2 = "aabbccddeeff"


def make_device(**over) -> Device:
    fields = dict(id=DEV, name="e-paper", type="epaper154", token_hash=None, created_at=NOW)
    fields.update(over)
    return Device(**fields)


def make_capture(**over) -> Capture:
    fields = dict(
        id=uuid.uuid4(), device_id=DEV, capture_id="cap_20260923_191530",
        recorded_at=datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC), date_estimated=False,
        received_at=NOW, day=date(2026, 9, 23),
        rel_path="2026/09/23/211530_70041dd8263c.wav", title=None, duration_s=1.0,
        size_bytes=32044, sha256="a" * 64, firmware_version="0.6.2", battery_pct=None,
        battery_v=4.1, power_source="usb", trashed_at=None,
    )
    fields.update(over)
    return Capture(**fields)
```

- [ ] **Step 4: Test (falliscono)**

`backend/tests/test_catalog.py`:

```python
import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy.exc import IntegrityError

from secondbrain import catalog
from secondbrain.models import Base
from tests.helpers import DEV, make_capture, make_device


def test_migrations_match_models(engine):
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == []


def test_same_capture_id_can_appear_twice(db):
    db.add(make_device())
    db.flush()
    db.add(make_capture())
    db.add(make_capture(rel_path="2026/09/23/211530_70041dd8263c_2.wav", sha256="b" * 64))
    db.commit()
    found = catalog.find_captures(db, DEV, "cap_20260923_191530")
    assert [c.sha256 for c in found] == ["a" * 64, "b" * 64]
    assert catalog.find_captures(db, DEV, "cap_20260101_000000") == []


def test_rel_path_is_unique(db):
    db.add(make_device())
    db.flush()
    db.add(make_capture())
    db.add(make_capture())
    with pytest.raises(IntegrityError):
        db.commit()


def test_get_capture_and_device(db):
    cap = make_capture()
    db.add(make_device())
    db.flush()
    db.add(cap)
    db.commit()
    assert catalog.get_capture(db, cap.id).rel_path == cap.rel_path
    assert catalog.get_device(db, DEV).name == "e-paper"
    assert catalog.get_capture(db, uuid.uuid4()) is None
```

Run: `docker compose -f docker-compose.test.yml up -d && uv run pytest tests/test_catalog.py -q`
Expected: FAIL, `ImportError` su `secondbrain.catalog`.

- [ ] **Step 5: Implementazione**

`backend/secondbrain/catalog.py`:

```python
"""Accesso al catalogo Postgres: engine, sessioni, query (spec §8)."""
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Capture, Device


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True)


def make_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def get_device(s: Session, device_id: str) -> Device | None:
    return s.get(Device, device_id)


def get_capture(s: Session, capture_id: uuid.UUID) -> Capture | None:
    return s.get(Capture, capture_id)


def find_captures(s: Session, device_id: str, capture_id: str) -> list[Capture]:
    """Tutte le registrazioni con quell'id del device, cestino compreso (spec §6 punto 5)."""
    stmt = (select(Capture)
            .where(Capture.device_id == device_id, Capture.capture_id == capture_id)
            .order_by(Capture.received_at, Capture.rel_path))
    return list(s.scalars(stmt))
```

- [ ] **Step 6: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS, compreso `test_migrations_match_models`.

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "backend: catalogo Postgres con migrazione iniziale di dispositivi e registrazioni

(device_id, capture_id) e' indicizzato ma non unico: il contatore delle catture
unsynced riparte da 1 se si cancella la NVS del device, e due registrazioni diverse
possono avere lo stesso id. Un test confronta migrazioni e modelli, cosi' non
divergono in silenzio. I test girano su un Postgres vero."
```

---

### Task 5: Sidecar e filesystem dell'archivio

**Files:**
- Create: `backend/secondbrain/sidecar.py`, `backend/secondbrain/archive.py`
- Test: `backend/tests/test_sidecar.py`, `backend/tests/test_archive.py`

**Interfaces:**
- Consumes: `naming.unique_base`, `tests.helpers.make_capture`.
- Produces: `sidecar.SCHEMA_VERSION = 1`, `sidecar.FIELDS` (tuple), `capture_to_sidecar(c) -> dict`, `sidecar_to_fields(d: dict) -> dict` (`ValueError` se versione o campi obbligatori mancano). `archive.INCOMING = ".incoming"`, `archive.TRASH = ".trash"`, `ArchiveError(ValueError)`, `Archive(root: Path)` con: `root`, `incoming_dir`, `trash_dir`, `ensure()`, `abs(rel) -> Path`, `new_incoming() -> Path`, `clean_incoming() -> int`, `reserve_base(dir_rel, base) -> str`, `commit_capture(tmp: Path, dir_rel, base, sidecar: dict) -> str` (rel del WAV), `write_sidecar(rel_wav, data)`, `read_sidecar(rel_wav) -> dict`, `related_files(rel_wav) -> list[str]` (nomi ordinati), `move(rel_wav, dest_dir_rel) -> str` (nuovo rel del WAV), `delete(rel_wav)`, `iter_sidecar_rels() -> Iterator[str]`, `iter_wavs_without_sidecar() -> Iterator[str]`.

- [ ] **Step 1: Test del sidecar (falliscono)**

`backend/tests/test_sidecar.py`:

```python
import json

import pytest

from secondbrain.sidecar import SCHEMA_VERSION, capture_to_sidecar, sidecar_to_fields
from tests.helpers import NOW, make_capture


def test_round_trip_through_json():
    cap = make_capture(title="Idea per il digest", trashed_at=NOW, battery_pct=95)
    data = json.loads(json.dumps(capture_to_sidecar(cap)))
    assert data["schema_version"] == SCHEMA_VERSION
    assert "rel_path" not in data and "day" not in data
    fields = sidecar_to_fields(data)
    for key, value in fields.items():
        assert getattr(cap, key) == value, key


def test_unknown_schema_version():
    data = capture_to_sidecar(make_capture())
    data["schema_version"] = 99
    with pytest.raises(ValueError, match="schema"):
        sidecar_to_fields(data)


def test_missing_required_field():
    data = capture_to_sidecar(make_capture())
    del data["sha256"]
    with pytest.raises(ValueError, match="sha256"):
        sidecar_to_fields(data)
```

Run: `uv run pytest tests/test_sidecar.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 2: Implementazione del sidecar**

`backend/secondbrain/sidecar.py`:

```python
"""Sidecar JSON di una registrazione (spec §4): conversioni pure.

`rel_path` e `day` non ci sono: si ricavano dalla posizione del file.
"""
import uuid
from datetime import datetime

SCHEMA_VERSION = 1
FIELDS = (
    "id", "device_id", "capture_id", "recorded_at", "date_estimated", "received_at",
    "title", "duration_s", "size_bytes", "sha256", "firmware_version", "battery_pct",
    "battery_v", "power_source", "trashed_at",
)
DATETIME_FIELDS = ("recorded_at", "received_at", "trashed_at")
REQUIRED = ("id", "device_id", "capture_id", "recorded_at", "received_at",
            "duration_s", "size_bytes", "sha256")


def capture_to_sidecar(capture) -> dict:
    data: dict = {"schema_version": SCHEMA_VERSION}
    for name in FIELDS:
        value = getattr(capture, name)
        if isinstance(value, datetime):
            value = value.isoformat()
        elif isinstance(value, uuid.UUID):
            value = str(value)
        data[name] = value
    return data


def sidecar_to_fields(data: dict) -> dict:
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version non supportata: {data.get('schema_version')!r}")
    missing = [name for name in REQUIRED if data.get(name) is None]
    if missing:
        raise ValueError(f"campi obbligatori mancanti: {', '.join(missing)}")
    fields = {name: data.get(name) for name in FIELDS}
    fields["id"] = uuid.UUID(fields["id"])
    for name in DATETIME_FIELDS:
        if fields[name] is not None:
            fields[name] = datetime.fromisoformat(fields[name])
    fields["date_estimated"] = bool(fields["date_estimated"])
    return fields
```

- [ ] **Step 3: Test dell'archivio (falliscono)**

`backend/tests/test_archive.py`:

```python
import pytest

from secondbrain.archive import Archive, ArchiveError

DAY = "2026/09/28"


@pytest.fixture
def archive(tmp_path):
    a = Archive(tmp_path / "archive")
    a.ensure()
    return a


def put(archive, rel, content=b"x"):
    path = archive.root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_commit_capture(archive):
    tmp = archive.new_incoming()
    tmp.write_bytes(b"RIFF-dati")
    rel = archive.commit_capture(tmp, DAY, "091530_dev", {"a": 1})
    assert rel == f"{DAY}/091530_dev.wav"
    assert archive.abs(rel).read_bytes() == b"RIFF-dati"
    assert archive.read_sidecar(rel) == {"a": 1}
    assert list(archive.incoming_dir.iterdir()) == []


def test_reserve_base_skips_taken_names(archive):
    put(archive, f"{DAY}/091530_dev.wav")
    put(archive, f"{DAY}/091530_dev_2.json")
    assert archive.reserve_base(DAY, "091530_dev") == "091530_dev_3"
    assert archive.reserve_base("2026/09/29", "091530_dev") == "091530_dev"


def test_related_files_only_same_base(archive):
    for name in ["x.wav", "x.json", "x.transcript.md", "x_2.wav", "xy.wav"]:
        put(archive, f"{DAY}/{name}")
    assert archive.related_files(f"{DAY}/x.wav") == ["x.json", "x.transcript.md", "x.wav"]


def test_move_carries_derived_files_and_prunes_empty_dirs(archive):
    for name in ["x.wav", "x.json", "x.transcript.md"]:
        put(archive, f"{DAY}/{name}")
    new = archive.move(f"{DAY}/x.wav", "2026/10/01")
    assert new == "2026/10/01/x.wav"
    assert archive.related_files(new) == ["x.json", "x.transcript.md", "x.wav"]
    assert not (archive.root / "2026/09").exists()


def test_move_renames_every_related_file_on_collision(archive):
    for name in ["x.wav", "x.json", "x.transcript.md"]:
        put(archive, f"{DAY}/{name}")
    put(archive, "2026/10/01/x.wav")
    new = archive.move(f"{DAY}/x.wav", "2026/10/01")
    assert new == "2026/10/01/x_2.wav"
    assert archive.related_files(new) == ["x_2.json", "x_2.transcript.md", "x_2.wav"]


def test_move_to_same_dir_is_noop(archive):
    put(archive, f"{DAY}/x.wav")
    assert archive.move(f"{DAY}/x.wav", DAY) == f"{DAY}/x.wav"


def test_trash_round_trip_keeps_trash_root(archive):
    put(archive, f"{DAY}/x.wav")
    in_trash = archive.move(f"{DAY}/x.wav", f".trash/{DAY}")
    assert in_trash == f".trash/{DAY}/x.wav"
    archive.delete(in_trash)
    assert archive.trash_dir.is_dir()
    assert not (archive.trash_dir / "2026").exists()


def test_delete_removes_related_files(archive):
    for name in ["x.wav", "x.json", "x_2.wav"]:
        put(archive, f"{DAY}/{name}")
    archive.delete(f"{DAY}/x.wav")
    assert sorted(p.name for p in (archive.root / DAY).iterdir()) == ["x_2.wav"]


@pytest.mark.parametrize("rel", ["", "..", "../x.wav", "/etc/passwd", "2026/../../x.wav", "a\\b"])
def test_abs_rejects_paths_outside_archive(archive, rel):
    with pytest.raises(ArchiveError):
        archive.abs(rel)


def test_clean_incoming(archive):
    archive.new_incoming().write_bytes(b"a")
    archive.new_incoming().write_bytes(b"b")
    assert archive.clean_incoming() == 2
    assert archive.clean_incoming() == 0


def test_write_sidecar_overwrites_without_leftovers(archive):
    put(archive, f"{DAY}/x.wav")
    archive.write_sidecar(f"{DAY}/x.wav", {"v": 1})
    archive.write_sidecar(f"{DAY}/x.wav", {"v": 2})
    assert archive.read_sidecar(f"{DAY}/x.wav") == {"v": 2}
    assert list(archive.incoming_dir.iterdir()) == []


def test_iter_sidecars_and_orphan_wavs(archive):
    for rel in [f"{DAY}/a.wav", f"{DAY}/a.json", f"{DAY}/a.summary.json",
                ".trash/2026/09/27/b.wav", ".trash/2026/09/27/b.json",
                f"{DAY}/c.wav", ".incoming/z.json"]:
        put(archive, rel)
    assert list(archive.iter_sidecar_rels()) == [".trash/2026/09/27/b.wav", f"{DAY}/a.wav"]
    assert list(archive.iter_wavs_without_sidecar()) == [f"{DAY}/c.wav"]
```

Run: `uv run pytest tests/test_archive.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 4: Implementazione dell'archivio**

`backend/secondbrain/archive.py`:

```python
"""Filesystem dell'archivio (spec §4): scrittura atomica, sidecar, spostamenti.

Un file è correlato a una registrazione se si chiama `<base>.<qualcosa>`; il nome base
non contiene punti, quindi `x.wav` e `x_2.wav` non si confondono mai.
"""
import glob
import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path

from .naming import unique_base

INCOMING = ".incoming"
TRASH = ".trash"
SIDECAR_SUFFIX = ".json"


class ArchiveError(ValueError):
    """Percorso non valido per l'archivio."""


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _is_base_file(path: Path) -> bool:
    return path.name.count(".") == 1


class Archive:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    @property
    def incoming_dir(self) -> Path:
        return self.root / INCOMING

    @property
    def trash_dir(self) -> Path:
        return self.root / TRASH

    def ensure(self) -> None:
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.trash_dir.mkdir(parents=True, exist_ok=True)

    def abs(self, rel: str) -> Path:
        if not rel or rel.startswith("/") or "\\" in rel:
            raise ArchiveError(f"percorso non valido: {rel!r}")
        path = (self.root / rel).resolve()
        if self.root not in path.parents:
            raise ArchiveError(f"percorso fuori dall'archivio: {rel!r}")
        return path

    def new_incoming(self) -> Path:
        self.ensure()
        return self.incoming_dir / f"{uuid.uuid4().hex}.tmp"

    def clean_incoming(self) -> int:
        if not self.incoming_dir.is_dir():
            return 0
        removed = 0
        for path in self.incoming_dir.iterdir():
            if path.is_file():
                path.unlink()
                removed += 1
        return removed

    def _taken(self, directory: Path, base: str) -> bool:
        return any(directory.glob(glob.escape(base) + ".*"))

    def reserve_base(self, dir_rel: str, base: str) -> str:
        directory = self.abs(dir_rel)
        return unique_base(base, lambda b: self._taken(directory, b))

    def commit_capture(self, tmp: Path, dir_rel: str, base: str, sidecar: dict) -> str:
        directory = self.abs(dir_rel)
        directory.mkdir(parents=True, exist_ok=True)
        rel_wav = f"{dir_rel}/{base}.wav"
        self.write_sidecar(rel_wav, sidecar)
        os.replace(tmp, directory / f"{base}.wav")
        _fsync_dir(directory)
        return rel_wav

    def write_sidecar(self, rel_wav: str, data: dict) -> None:
        target = self.abs(rel_wav).with_suffix(SIDECAR_SUFFIX)
        self.ensure()
        tmp = self.incoming_dir / f"{uuid.uuid4().hex}.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)

    def read_sidecar(self, rel_wav: str) -> dict:
        with open(self.abs(rel_wav).with_suffix(SIDECAR_SUFFIX), encoding="utf-8") as f:
            return json.load(f)

    def related_files(self, rel_wav: str) -> list[str]:
        path = self.abs(rel_wav)
        pattern = glob.escape(path.stem) + ".*"
        return sorted(p.name for p in path.parent.glob(pattern) if p.is_file())

    def move(self, rel_wav: str, dest_dir_rel: str) -> str:
        src = self.abs(rel_wav)
        src_dir, base = src.parent, src.stem
        dest_dir = self.abs(dest_dir_rel)
        if dest_dir == src_dir:
            return rel_wav
        names = self.related_files(rel_wav)
        dest_dir.mkdir(parents=True, exist_ok=True)
        new_base = unique_base(base, lambda b: self._taken(dest_dir, b))
        for name in names:
            os.replace(src_dir / name, dest_dir / (new_base + name[len(base):]))
        _fsync_dir(dest_dir)
        self._prune(src_dir)
        return f"{dest_dir_rel}/{new_base}.wav"

    def delete(self, rel_wav: str) -> None:
        path = self.abs(rel_wav)
        for name in self.related_files(rel_wav):
            (path.parent / name).unlink()
        self._prune(path.parent)

    def _prune(self, directory: Path) -> None:
        keep = {self.root, self.trash_dir, self.incoming_dir}
        while directory not in keep and directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            directory = directory.parent

    def iter_sidecar_rels(self) -> Iterator[str]:
        """Percorso relativo del WAV per ogni sidecar `<base>.json`, cestino compreso."""
        for path in sorted(self.root.rglob("*" + SIDECAR_SUFFIX)):
            rel = path.relative_to(self.root)
            if rel.parts[0] == INCOMING or not path.is_file() or not _is_base_file(path):
                continue
            yield rel.with_suffix(".wav").as_posix()

    def iter_wavs_without_sidecar(self) -> Iterator[str]:
        for path in sorted(self.root.rglob("*.wav")):
            rel = path.relative_to(self.root)
            if rel.parts[0] == INCOMING or not _is_base_file(path):
                continue
            if not path.with_suffix(SIDECAR_SUFFIX).exists():
                yield rel.as_posix()
```

- [ ] **Step 5: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/secondbrain/sidecar.py backend/secondbrain/archive.py backend/tests/test_sidecar.py backend/tests/test_archive.py
git commit -m "backend: archivio su disco con sidecar JSON e spostamenti che portano i derivati

Il WAV arriva in .incoming e viene rinominato nella cartella del giorno solo quando
il sidecar e' gia' scritto: non esiste mai un WAV archiviato senza metadati. Spostare
o cestinare una registrazione porta con se' tutti i file con lo stesso nome base,
cosi' trascrizioni e riassunti futuri non restano indietro."
```

---

### Task 6: Dispositivi, token e primi comandi CLI

**Files:**
- Create: `backend/secondbrain/devices.py`, `backend/secondbrain/cli.py`
- Modify: `backend/secondbrain/catalog.py` (aggiunge `list_devices`)
- Test: `backend/tests/test_devices.py`, `backend/tests/test_cli.py`

**Interfaces:**
- Consumes: `naming.DEFAULT_DEVICE_TYPE`, `naming.is_valid_device_id`, `naming.is_valid_device_type`, `models.Device`, `catalog.make_engine`, `catalog.make_sessionmaker`, `config.load_settings`.
- Produces: `devices.DeviceError(ValueError)`, `devices.AuthError(status: int, detail: str)`, `generate_token() -> str`, `hash_token(token) -> str`, `bearer_token(headers: Mapping[str, str]) -> str | None`, `create_device(s, device_id, name, type_, now) -> tuple[Device, str]`, `regenerate_token(s, device_id) -> str`, `rename_device(s, device_id, name) -> Device`, `authenticate(s, token, device_id, allow_unauth, now) -> Device`, `authenticate_any(s, token, allow_unauth) -> Device | None`, `DeviceMeta(firmware_version, battery_pct, battery_v, power_source)` con `DeviceMeta.from_headers(h)`, `record_seen(device, meta, now)`. `catalog.list_devices(s) -> list[Device]`. `cli.main(argv) -> int`, `cli.open_session()` (context manager → `(Session, Settings)`), `cli.CliError`, `cli.COMMAND_GROUPS` (tupla di funzioni `(subparsers) -> None`).

- [ ] **Step 1: Test dei dispositivi (falliscono)**

`backend/tests/test_devices.py`:

```python
import pytest

from secondbrain.devices import (AuthError, DeviceError, DeviceMeta, authenticate,
                                 authenticate_any, bearer_token, create_device,
                                 generate_token, hash_token, record_seen,
                                 regenerate_token, rename_device)
from secondbrain.models import Device
from tests.helpers import DEV, DEV2, NOW


def test_token_and_hash():
    a, b = generate_token(), generate_token()
    assert a != b and len(a) >= 40
    assert hash_token(a) == hash_token(a)
    assert len(hash_token(a)) == 64


@pytest.mark.parametrize("headers,expected", [
    ({"authorization": "Bearer abc"}, "abc"),
    ({"authorization": "bearer  abc "}, "abc"),
    ({"authorization": "Basic abc"}, None),
    ({"authorization": "Bearer "}, None),
    ({}, None),
])
def test_bearer_token(headers, expected):
    assert bearer_token(headers) == expected


def test_create_device_stores_only_the_hash(db):
    dev, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    db.commit()
    assert dev.token_hash == hash_token(token)
    assert db.get(Device, DEV).name == "e-paper"


def test_create_device_twice_fails(db):
    create_device(db, DEV, "e-paper", "epaper154", NOW)
    with pytest.raises(DeviceError, match="già registrato"):
        create_device(db, DEV, "altro", "epaper154", NOW)


@pytest.mark.parametrize("device_id,type_", [("70:04:1d:d8:26:3c", "epaper154"), (DEV, "E Paper")])
def test_create_device_validates(db, device_id, type_):
    with pytest.raises(DeviceError):
        create_device(db, device_id, "x", type_, NOW)


def test_authenticate_with_token(db):
    dev, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    assert authenticate(db, token, DEV, False, NOW) is dev


def test_authenticate_wrong_token(db):
    create_device(db, DEV, "e-paper", "epaper154", NOW)
    with pytest.raises(AuthError) as err:
        authenticate(db, "sbagliato", DEV, False, NOW)
    assert err.value.status == 401


def test_token_of_another_device_is_forbidden(db):
    _, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    with pytest.raises(AuthError) as err:
        authenticate(db, token, DEV2, False, NOW)
    assert err.value.status == 403


def test_missing_token_rejected(db):
    with pytest.raises(AuthError) as err:
        authenticate(db, None, DEV, False, NOW)
    assert err.value.status == 401


def test_missing_token_allowed_on_lan_registers_device(db):
    dev = authenticate(db, None, DEV, True, NOW)
    db.commit()
    assert (dev.type, dev.token_hash, dev.name) == ("epaper154", None, DEV)
    assert db.get(Device, DEV) is not None


def test_lan_mode_still_validates_device_id(db):
    with pytest.raises(AuthError) as err:
        authenticate(db, None, "../x", True, NOW)
    assert err.value.status == 400


def test_regenerate_token_invalidates_old_one(db):
    _, old = create_device(db, DEV, "e-paper", "epaper154", NOW)
    new = regenerate_token(db, DEV)
    assert authenticate(db, new, DEV, False, NOW).id == DEV
    with pytest.raises(AuthError):
        authenticate(db, old, DEV, False, NOW)
    with pytest.raises(DeviceError):
        regenerate_token(db, DEV2)


def test_authenticate_any(db):
    dev, token = create_device(db, DEV, "e-paper", "epaper154", NOW)
    assert authenticate_any(db, token, False) is dev
    assert authenticate_any(db, None, True) is None
    for token_, allow in [(None, False), ("sbagliato", True)]:
        with pytest.raises(AuthError) as err:
            authenticate_any(db, token_, allow)
        assert err.value.status == 401


def test_device_meta_from_headers():
    meta = DeviceMeta.from_headers({
        "x-firmware-version": "0.6.2", "x-battery-pct": "95",
        "x-battery-voltage": "4.15", "x-power-source": "Battery",
    })
    assert meta == DeviceMeta("0.6.2", 95, 4.15, "battery")
    junk = DeviceMeta.from_headers({
        "x-battery-pct": "abc", "x-battery-voltage": "nan", "x-power-source": "solar",
    })
    assert junk == DeviceMeta()
    assert DeviceMeta.from_headers({"x-battery-pct": "150"}).battery_pct is None


def test_record_seen_and_rename(db):
    dev, _ = create_device(db, DEV, "e-paper", "epaper154", NOW)
    record_seen(dev, DeviceMeta("0.6.2", None, 4.9, "usb"), NOW)
    assert (dev.last_seen_at, dev.last_firmware, dev.last_power_source) == (NOW, "0.6.2", "usb")
    assert rename_device(db, DEV, "  scrivania  ").name == "scrivania"
    with pytest.raises(DeviceError):
        rename_device(db, DEV, "   ")
    with pytest.raises(DeviceError):
        rename_device(db, DEV2, "x")
```

Run: `uv run pytest tests/test_devices.py -q`
Expected: FAIL, `ImportError`.

- [ ] **Step 2: Implementazione dei dispositivi**

`backend/secondbrain/devices.py`:

```python
"""Dispositivi: token, autenticazione, metadati dagli header (spec §5, §6 punto 1)."""
import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Device
from .naming import DEFAULT_DEVICE_TYPE, is_valid_device_id, is_valid_device_type

TOKEN_BYTES = 32
POWER_SOURCES = ("battery", "usb")
MAX_FIRMWARE_LEN = 32
MAX_NAME_LEN = 100
MAX_BATTERY_V = 10.0


class DeviceError(ValueError):
    """Operazione non valida su un dispositivo."""


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def generate_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    # Il token è casuale a 256 bit: basta un hash veloce, non serve un KDF.
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(headers: Mapping[str, str]) -> str | None:
    scheme, _, value = headers.get("authorization", "").strip().partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()


def create_device(s: Session, device_id: str, name: str, type_: str,
                  now: datetime) -> tuple[Device, str]:
    if not is_valid_device_id(device_id):
        raise DeviceError(f"id non valido: {device_id!r} (il MAC in 12 cifre esadecimali minuscole)")
    if not is_valid_device_type(type_):
        raise DeviceError(f"tipo non valido: {type_!r}")
    if s.get(Device, device_id) is not None:
        raise DeviceError(f"dispositivo {device_id} già registrato: usa 'device token' per un nuovo token")
    token = generate_token()
    device = Device(id=device_id, name=name.strip()[:MAX_NAME_LEN] or device_id, type=type_,
                    token_hash=hash_token(token), created_at=now)
    s.add(device)
    s.flush()
    return device, token


def _require(s: Session, device_id: str) -> Device:
    device = s.get(Device, device_id)
    if device is None:
        raise DeviceError(f"dispositivo {device_id} sconosciuto")
    return device


def regenerate_token(s: Session, device_id: str) -> str:
    device = _require(s, device_id)
    token = generate_token()
    device.token_hash = hash_token(token)
    s.flush()
    return token


def rename_device(s: Session, device_id: str, name: str) -> Device:
    device = _require(s, device_id)
    clean = name.strip()[:MAX_NAME_LEN]
    if not clean:
        raise DeviceError("il nome non può essere vuoto")
    device.name = clean
    s.flush()
    return device


def _by_token(s: Session, token: str) -> Device | None:
    return s.scalar(select(Device).where(Device.token_hash == hash_token(token)))


def authenticate(s: Session, token: str | None, device_id: str, allow_unauth: bool,
                 now: datetime) -> Device:
    """Dispositivo autorizzato a caricare come `device_id`, oppure AuthError."""
    if token:
        device = _by_token(s, token)
        if device is None:
            raise AuthError(401, "token non valido")
        if device.id != device_id:
            raise AuthError(403, "X-Device-Id non corrisponde al token")
        return device
    if not allow_unauth:
        raise AuthError(401, "token mancante")
    if not is_valid_device_id(device_id):
        raise AuthError(400, "X-Device-Id mancante o non valido")
    device = s.get(Device, device_id)
    if device is None:
        device = Device(id=device_id, name=device_id, type=DEFAULT_DEVICE_TYPE,
                        token_hash=None, created_at=now)
        s.add(device)
        s.flush()
    return device


def authenticate_any(s: Session, token: str | None, allow_unauth: bool) -> Device | None:
    """Per le richieste senza X-Device-Id (firmware OTA)."""
    if token:
        device = _by_token(s, token)
        if device is None:
            raise AuthError(401, "token non valido")
        return device
    if allow_unauth:
        return None
    raise AuthError(401, "token mancante")


def _pct(raw: str | None) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if 0 <= value <= 100 else None


def _volts(raw: str | None) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if 0.0 < value < MAX_BATTERY_V else None  # NaN non passa


def _source(raw: str | None) -> str | None:
    value = (raw or "").strip().lower()
    return value if value in POWER_SOURCES else None


@dataclass(frozen=True)
class DeviceMeta:
    firmware_version: str | None = None
    battery_pct: int | None = None
    battery_v: float | None = None
    power_source: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "DeviceMeta":
        firmware = (headers.get("x-firmware-version") or "").strip()[:MAX_FIRMWARE_LEN]
        return cls(
            firmware_version=firmware or None,
            battery_pct=_pct(headers.get("x-battery-pct")),
            battery_v=_volts(headers.get("x-battery-voltage")),
            power_source=_source(headers.get("x-power-source")),
        )


def record_seen(device: Device, meta: DeviceMeta, now: datetime) -> None:
    device.last_seen_at = now
    device.last_firmware = meta.firmware_version
    device.last_battery_pct = meta.battery_pct
    device.last_battery_v = meta.battery_v
    device.last_power_source = meta.power_source
```

Aggiungere a `backend/secondbrain/catalog.py`:

```python
def list_devices(s: Session) -> list[Device]:
    return list(s.scalars(select(Device).order_by(Device.name, Device.id)))
```

- [ ] **Step 3: Test della CLI (falliscono)**

`backend/tests/test_cli.py`:

```python
import pytest

from secondbrain.cli import main
from secondbrain.devices import hash_token
from secondbrain.models import Device
from tests.helpers import DEV, TEST_DB


@pytest.fixture
def cli_db(monkeypatch, db):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    return db


def last_value(out: str) -> str:
    return out.strip().splitlines()[-1].split(": ", 1)[1]


def test_device_add_prints_token(cli_db, capsys):
    assert main(["device", "add", DEV, "--name", "e-paper"]) == 0
    token = last_value(capsys.readouterr().out)
    assert cli_db.get(Device, DEV).token_hash == hash_token(token)


def test_device_add_duplicate_fails(cli_db, capsys):
    main(["device", "add", DEV])
    assert main(["device", "add", DEV]) == 1
    assert "già registrato" in capsys.readouterr().err


def test_device_token_and_list(cli_db, capsys):
    main(["device", "add", DEV, "--name", "e-paper"])
    assert main(["device", "token", DEV]) == 0
    token = last_value(capsys.readouterr().out)
    assert cli_db.get(Device, DEV).token_hash == hash_token(token)
    assert main(["device", "list"]) == 0
    out = capsys.readouterr().out
    assert "e-paper" in out and "token=sì" in out


def test_missing_database_url(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert main(["device", "list"]) == 1
    assert "DATABASE_URL" in capsys.readouterr().err
```

Run: `uv run pytest tests/test_cli.py -q`
Expected: FAIL, `ImportError` su `secondbrain.cli`.

- [ ] **Step 4: Implementazione della CLI**

`backend/secondbrain/cli.py`:

```python
"""Comandi di amministrazione: `secondbrain <gruppo> <azione>` (spec §5).

Ogni gruppo di comandi è una funzione `_add_*_commands(subparsers)` elencata in
COMMAND_GROUPS; ogni azione riceve gli argomenti e solleva CliError in caso di errore.
"""
import argparse
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from sqlalchemy.orm import Session

from . import catalog, devices
from .clock import utcnow
from .config import ConfigError, Settings, load_settings
from .naming import DEFAULT_DEVICE_TYPE


class CliError(Exception):
    """Errore da mostrare all'utente, senza traceback."""


@contextmanager
def open_session() -> Iterator[tuple[Session, Settings]]:
    settings = load_settings()
    engine = catalog.make_engine(settings.database_url)
    try:
        with catalog.make_sessionmaker(engine)() as session:
            yield session, settings
    finally:
        engine.dispose()


def _device_add(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        try:
            device, token = devices.create_device(s, args.id, args.name or args.id,
                                                  args.type, utcnow())
        except devices.DeviceError as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print(f"Dispositivo {device.id} ({device.name}) registrato.")
    print(f"Token (mostrato solo ora): {token}")


def _device_token(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        try:
            token = devices.regenerate_token(s, args.id)
        except devices.DeviceError as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print(f"Nuovo token per {args.id}; il precedente non vale più.")
    print(f"Token (mostrato solo ora): {token}")


def _device_list(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        for d in catalog.list_devices(s):
            seen = d.last_seen_at.isoformat(timespec="seconds") if d.last_seen_at else "mai"
            print(f"{d.id}  {d.name}  tipo={d.type}  token={'sì' if d.token_hash else 'no'}"
                  f"  ultimo contatto={seen}  firmware={d.last_firmware or '-'}")


def _add_device_commands(sub) -> None:
    group = sub.add_parser("device", help="dispositivi e token")
    actions = group.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add", help="registra un dispositivo e genera il token")
    add.add_argument("id", help="MAC del dispositivo, 12 cifre esadecimali minuscole")
    add.add_argument("--name", help="nome leggibile")
    add.add_argument("--type", default=DEFAULT_DEVICE_TYPE, help="tipo di dispositivo")
    add.set_defaults(func=_device_add)
    token = actions.add_parser("token", help="nuovo token (il precedente smette di valere)")
    token.add_argument("id")
    token.set_defaults(func=_device_token)
    actions.add_parser("list", help="elenca i dispositivi").set_defaults(func=_device_list)


COMMAND_GROUPS: tuple[Callable, ...] = (_add_device_commands,)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secondbrain")
    sub = parser.add_subparsers(dest="command", required=True)
    for add_group in COMMAND_GROUPS:
        add_group(sub)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (CliError, ConfigError) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS. Verifica a mano: `uv run secondbrain --help` elenca il gruppo `device`.

- [ ] **Step 6: Commit**

```bash
git add backend/
git commit -m "backend: dispositivi con token e comandi CLI device add/token/list

Il token si salva solo come hash e si mostra una volta sola. In modalita' LAN senza
autenticazione (solo sviluppo, per il firmware attuale che non manda token) un device
sconosciuto si registra da solo alla prima cattura; la modalita' e' spenta di default.
I metadati di batteria e firmware dagli header sono tollerati se strani: un valore
fuori scala diventa nullo invece di far rifiutare la cattura."
```

---
### Task 7: Ricezione `POST /captures` e applicazione FastAPI

**Files:**
- Create: `backend/secondbrain/httputil.py`, `backend/secondbrain/ingest.py`, `backend/secondbrain/app.py`
- Modify: `backend/tests/conftest.py` (fixture dell'app), `backend/tests/helpers.py` (`capture_headers`, `upload`)
- Test: `backend/tests/test_ingest.py`

**Interfaces:**
- Consumes: tutto quanto sopra (`naming`, `wav`, `sidecar`, `archive.Archive`, `catalog`, `devices`).
- Produces: `create_app(settings: Settings, clock: Callable[[], datetime] = utcnow) -> FastAPI` con `app.state.settings`, `.clock`, `.archive` (`Archive`), `.engine`, `.sessionmaker`; `create_app_from_env() -> FastAPI` (per uvicorn `--factory`); route `POST /captures` e `GET /healthz`; `app.DEVICE_PATHS`; `httputil.public_host(request) -> str`, `public_scheme(request) -> str`, `public_base_url(request) -> str`, `is_lan_request(request) -> bool` (spec §5: IP privato/loopback e nessun `Cf-Connecting-Ip`); `ingest._commit(session)` (punto di iniezione per i test). Fixture: `clock` (`FakeClock` con attributo `now` modificabile), `settings`, `make_client(settings, client_addr=LAN_CLIENT) -> TestClient` (il `TestClient` si presenta con l'indirizzo dato; di default un IP della LAN), `client` (token obbligatorio), `lan_client` (`allow_unauthenticated_lan=True`), `token` (registra `DEV` e ne restituisce il token). Helper: `capture_headers(token=None, capture_id=..., ts=..., device=DEV, extra=None) -> dict`, `upload(client, wav=None, **kw) -> Response`.

- [ ] **Step 1: Fixture e helper**

Aggiungere a `backend/tests/conftest.py` (import in cima):

```python
from dataclasses import replace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from secondbrain.app import create_app
from secondbrain.config import Settings
from secondbrain.devices import create_device
from tests.helpers import DEV, LAN_CLIENT, NOW


class FakeClock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return FakeClock(NOW)


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=TEST_DB, archive_dir=tmp_path / "archive",
                    firmware_dir=tmp_path / "firmware", tz_archive=ZoneInfo("Europe/Rome"))


@pytest.fixture
def make_client(db, clock):
    opened = []

    def factory(settings, client_addr=LAN_CLIENT):
        client = TestClient(create_app(settings, clock=clock), client=client_addr)
        client.__enter__()  # esegue il lifespan
        opened.append(client)
        return client

    yield factory
    for client in opened:
        client.__exit__(None, None, None)


@pytest.fixture
def client(make_client, settings):
    return make_client(settings)


@pytest.fixture
def lan_client(make_client, settings):
    return make_client(replace(settings, allow_unauthenticated_lan=True))


@pytest.fixture
def token(db):
    _, tok = create_device(db, DEV, "e-paper", "epaper154", NOW)
    db.commit()
    return tok
```

Aggiungere in fondo a `backend/tests/helpers.py`:

```python
LAN_CLIENT = ("192.168.1.50", 50000)
INTERNET_CLIENT = ("203.0.113.7", 50000)


def capture_headers(token: str | None = None, capture_id: str = "cap_20260923_191530",
                    ts: str | None = "2026-09-23T19:15:30Z", device: str = DEV,
                    extra: dict | None = None) -> dict:
    headers = {
        "Content-Type": "audio/wav", "X-Capture-Id": capture_id, "X-Device-Id": device,
        "X-Firmware-Version": "0.6.2", "X-Battery-Voltage": "4.15", "X-Power-Source": "usb",
    }
    if ts is not None:
        headers["X-Capture-Ts"] = ts
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(extra or {})
    return headers


def upload(client, wav: bytes | None = None, **kw):
    body = make_wav() if wav is None else wav
    return client.post("/captures", content=body, headers=capture_headers(**kw))
```

- [ ] **Step 2: Test della ricezione (falliscono)**

`backend/tests/test_ingest.py`:

```python
import errno
import json
from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from secondbrain import ingest
from secondbrain.archive import Archive
from secondbrain.models import Capture, Device
from starlette.requests import Request

from secondbrain.httputil import is_lan_request
from tests.helpers import DEV, DEV2, INTERNET_CLIENT, NOW, capture_headers, make_wav, upload

BASE = "211530_70041dd8263c"


def captures(db):
    db.expire_all()
    return list(db.scalars(select(Capture).order_by(Capture.rel_path)))


def files(settings, day="2026/09/23"):
    directory = settings.archive_dir / day
    return sorted(p.name for p in directory.iterdir()) if directory.exists() else []


def incoming(settings):
    return list((settings.archive_dir / ".incoming").iterdir())


def test_accepted(client, db, settings, token):
    wav = make_wav(1.5)
    r = upload(client, wav, token=token)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "accepted"
    assert body["path"] == f"2026/09/23/{BASE}.wav"
    assert (settings.archive_dir / body["path"]).read_bytes() == wav
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]
    [cap] = captures(db)
    assert str(cap.id) == body["id"]
    assert (cap.day, cap.date_estimated, cap.size_bytes) == (date(2026, 9, 23), False, len(wav))
    assert cap.recorded_at == datetime(2026, 9, 23, 19, 15, 30, tzinfo=UTC)
    assert cap.duration_s == pytest.approx(1.5)
    sidecar = json.loads((settings.archive_dir / f"2026/09/23/{BASE}.json").read_text())
    assert sidecar["sha256"] == cap.sha256 and sidecar["id"] == body["id"]
    device = db.get(Device, DEV)
    assert (device.last_seen_at, device.last_firmware, device.last_power_source) == (NOW, "0.6.2", "usb")
    assert incoming(settings) == []


def test_duplicate_returns_409(client, db, settings, token):
    first = upload(client, token=token).json()
    r = upload(client, token=token)
    assert r.status_code == 409
    assert r.json() == {"id": first["id"], "status": "duplicate"}
    assert len(captures(db)) == 1
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]
    assert incoming(settings) == []


def test_same_id_with_different_content_is_archived_too(client, db, token):
    upload(client, make_wav(fill=b"\x01\x00"), token=token)
    r = upload(client, make_wav(fill=b"\x02\x00"), token=token)
    assert r.status_code == 201
    assert r.json()["path"] == f"2026/09/23/{BASE}_2.wav"


def test_unsynced_capture_gets_estimated_date(client, db, token):
    r = upload(client, token=token, capture_id="cap_unsynced_000001", ts=None)
    assert r.json()["path"] == "2026/09/28/140000_70041dd8263c.wav"
    [cap] = captures(db)
    assert cap.date_estimated and cap.recorded_at == NOW


def test_implausible_timestamp_is_estimated(client, db, token):
    upload(client, token=token, ts="2000-01-01T00:00:05Z")
    assert captures(db)[0].date_estimated


def test_dst_fall_back_keeps_both_recordings(client, clock, token):
    clock.now = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
    a = upload(client, make_wav(fill=b"\x01\x00"), token=token,
               capture_id="cap_20261025_003000", ts="2026-10-25T00:30:00Z")
    b = upload(client, make_wav(fill=b"\x02\x00"), token=token,
               capture_id="cap_20261025_013000", ts="2026-10-25T01:30:00Z")
    assert [a.json()["path"], b.json()["path"]] == [
        "2026/10/25/023000_70041dd8263c.wav", "2026/10/25/023000_70041dd8263c_2.wav"]


def test_two_devices_in_the_same_second(lan_client, settings):
    assert upload(lan_client).status_code == 201
    assert upload(lan_client, device=DEV2).status_code == 201
    assert [n for n in files(settings) if n.endswith(".wav")] == [
        "211530_70041dd8263c.wav", "211530_aabbccddeeff.wav"]


@pytest.mark.parametrize("kw", [{}, {"token": "sbagliato"}])
def test_token_required(client, token, kw):
    assert upload(client, **kw).status_code == 401


def test_token_of_another_device(client, token):
    assert upload(client, token=token, device=DEV2).status_code == 403


def test_lan_mode_registers_unknown_device(lan_client, db):
    assert upload(lan_client).status_code == 201
    assert db.get(Device, DEV) is not None


def test_lan_mode_refuses_internet_clients(make_client, settings):
    c = make_client(replace(settings, allow_unauthenticated_lan=True), client_addr=INTERNET_CLIENT)
    assert upload(c).status_code == 401


def test_lan_mode_refuses_requests_through_the_tunnel(lan_client):
    r = upload(lan_client, extra={"Cf-Connecting-Ip": "203.0.113.7"})
    assert r.status_code == 401


def _request(client, headers=()):
    return Request({"type": "http", "client": client,
                    "headers": [(k.encode(), v.encode()) for k, v in headers]})


@pytest.mark.parametrize("client,headers,expected", [
    (("192.168.1.5", 1), (), True),
    (("10.0.0.2", 1), (), True),
    (("172.18.0.3", 1), (), True),          # rete interna di Docker
    (("127.0.0.1", 1), (), True),
    (("::ffff:192.168.1.5", 1), (), True),
    (("203.0.113.7", 1), (), False),
    (("192.168.1.5", 1), (("cf-connecting-ip", "203.0.113.7"),), False),
    (("testclient", 1), (), False),
    (None, (), False),
])
def test_is_lan_request(client, headers, expected):
    assert is_lan_request(_request(client, headers)) is expected


def test_invalid_capture_id(client, token):
    assert upload(client, token=token, capture_id="../../x").status_code == 400


def test_too_large(make_client, settings, token):
    small = make_client(replace(settings, max_upload_bytes=1024))
    assert upload(small, token=token).status_code == 413


def test_missing_content_length(client, token):
    r = client.post("/captures", content=iter([make_wav()]), headers=capture_headers(token=token))
    assert r.status_code == 411


def test_not_a_wav(client, db, settings, token):
    assert upload(client, b"non e' audio" * 10, token=token).status_code == 422
    assert captures(db) == [] and files(settings) == [] and incoming(settings) == []


def test_trashed_capture_still_counts_as_duplicate(client, db, token):
    upload(client, token=token)
    [cap] = captures(db)
    cap.trashed_at = NOW
    db.commit()
    assert upload(client, token=token).status_code == 409


def test_failed_commit_removes_archived_files(client, db, settings, token, monkeypatch):
    def boom(session):
        raise OperationalError("COMMIT", {}, Exception("db giù"))

    monkeypatch.setattr(ingest, "_commit", boom)
    assert upload(client, token=token).status_code == 503
    assert files(settings) == [] and captures(db) == [] and incoming(settings) == []
    monkeypatch.undo()
    assert upload(client, token=token).status_code == 201
    assert files(settings) == [f"{BASE}.json", f"{BASE}.wav"]


def test_disk_full_returns_507(client, settings, token, monkeypatch):
    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(Archive, "commit_capture", full)
    assert upload(client, token=token).status_code == 507
    assert incoming(settings) == []


def test_incoming_is_cleaned_at_startup(make_client, settings):
    inc = settings.archive_dir / ".incoming"
    inc.mkdir(parents=True)
    (inc / "vecchio.tmp").write_bytes(b"x")
    make_client(settings)
    assert list(inc.iterdir()) == []


def test_device_hostname_only_serves_device_paths(make_client, settings, token):
    c = make_client(replace(settings, device_hostname="ingest.example.org"))
    assert c.get("/healthz").status_code == 200
    for host in ["ingest.example.org", "ingest.example.org:443", "INGEST.example.org"]:
        assert c.get("/healthz", headers={"Host": host}).status_code == 404
    r = c.post("/captures", content=make_wav(),
               headers=capture_headers(token=token, extra={"Host": "ingest.example.org"}))
    assert r.status_code == 201
```

Run: `uv run pytest tests/test_ingest.py -q`
Expected: FAIL, `ImportError` su `secondbrain.app`.

- [ ] **Step 3: `httputil`**

`backend/secondbrain/httputil.py`:

```python
"""Host e schema con cui il client ha raggiunto il servizio.

Si usa solo l'header Host (cloudflared lo imposta all'hostname pubblico); X-Forwarded-Host
lo può falsificare chiunque e non viene letto. X-Forwarded-Proto serve per sapere se
dietro il tunnel la richiesta era HTTPS.
"""
import ipaddress

from starlette.requests import Request


def _host_header(request: Request) -> str:
    return request.headers.get("host", "").strip()


def public_host(request: Request) -> str:
    return _host_header(request).split(":")[0].lower()


def public_scheme(request: Request) -> str:
    raw = request.headers.get("x-forwarded-proto")
    if raw:
        return raw.split(",")[0].strip().lower()
    return request.url.scheme


def public_base_url(request: Request) -> str:
    return f"{public_scheme(request)}://{_host_header(request)}"


def is_lan_request(request: Request) -> bool:
    """Richiesta dalla rete locale: IP privato o loopback e non passata dal tunnel.

    Cloudflare aggiunge sempre Cf-Connecting-Ip (sovrascrivendo quello del client), e
    le richieste del tunnel arrivano da cloudflared, che ha anch'esso un IP privato:
    è l'header, non l'IP, a distinguerle.
    """
    if "cf-connecting-ip" in request.headers or request.client is None:
        return False
    try:
        ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_private or ip.is_loopback
```

- [ ] **Step 4: `ingest`**

`backend/secondbrain/ingest.py`:

```python
"""POST /captures: ricezione delle registrazioni dai dispositivi (spec §6)."""
import errno
import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from starlette.datastructures import Headers
from starlette.requests import ClientDisconnect

from . import catalog, naming
from .archive import Archive
from .devices import AuthError, DeviceMeta, authenticate, bearer_token, record_seen
from .httputil import is_lan_request
from .models import Capture
from .sidecar import capture_to_sidecar
from .wav import HEADER_PROBE_BYTES, WavError, parse_wav_header

log = logging.getLogger(__name__)
router = APIRouter()


class Reject(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _commit(session: Session) -> None:
    session.commit()


def _content_length(headers: Headers, maximum: int) -> int:
    raw = headers.get("content-length")
    if raw is None:
        raise Reject(411, "Content-Length obbligatorio")
    try:
        length = int(raw)
    except ValueError:
        raise Reject(400, "Content-Length non valido") from None
    if length > maximum:
        raise Reject(413, f"file oltre {maximum} byte")
    return length


async def _receive(request: Request, archive: Archive, maximum: int) -> tuple[Path, int, str]:
    tmp = archive.new_incoming()
    hasher = hashlib.sha256()
    size = 0
    try:
        with open(tmp, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > maximum:
                    raise Reject(413, f"file oltre {maximum} byte")
                hasher.update(chunk)
                f.write(chunk)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return tmp, size, hasher.hexdigest()


def _authenticate(state, token: str | None, device_id: str, meta: DeviceMeta,
                  now: datetime, allow_unauth: bool) -> None:
    with state.sessionmaker() as s:
        device = authenticate(s, token, device_id, allow_unauth, now)
        record_seen(device, meta, now)
        s.commit()


def _store(state, device_id: str, capture_id: str, ts_header: str | None, meta: DeviceMeta,
           tmp: Path, size: int, sha: str, now: datetime) -> tuple[int, dict]:
    with open(tmp, "rb") as f:
        head = f.read(HEADER_PROBE_BYTES)
    try:
        info = parse_wav_header(head, size)
    except WavError as exc:
        raise Reject(422, f"WAV non valido: {exc}") from None
    tz = state.settings.tz_archive
    archive: Archive = state.archive
    with state.sessionmaker() as s:
        previous = catalog.find_captures(s, device_id, capture_id)
        for p in previous:
            if p.sha256 == sha:
                return 409, {"id": str(p.id), "status": "duplicate"}
        if previous:
            log.warning("%s di %s già presente con contenuto diverso: archiviata come nuova",
                        capture_id, device_id)
        recorded_at, estimated = naming.resolve_recorded_at(ts_header, now)
        day = naming.local_day(recorded_at, tz)
        dir_rel = naming.day_dir(day)
        base = archive.reserve_base(dir_rel, naming.base_name(recorded_at, tz, device_id))
        capture = Capture(
            id=uuid.uuid4(), device_id=device_id, capture_id=capture_id,
            recorded_at=recorded_at, date_estimated=estimated, received_at=now, day=day,
            rel_path=f"{dir_rel}/{base}.wav", title=None, duration_s=info.duration_s,
            size_bytes=size, sha256=sha, firmware_version=meta.firmware_version,
            battery_pct=meta.battery_pct, battery_v=meta.battery_v,
            power_source=meta.power_source, trashed_at=None,
        )
        rel = archive.commit_capture(tmp, dir_rel, base, capture_to_sidecar(capture))
        s.add(capture)
        try:
            _commit(s)
        except Exception:
            # Senza riga il device ritenterà: i file non devono restare, o nascerebbe un doppione.
            archive.delete(rel)
            raise
    log.info("archiviata %s (%s, %d byte)", rel, capture_id, size)
    return 201, {"id": str(capture.id), "path": rel, "status": "accepted"}


def _error(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


@router.post("/captures")
async def post_capture(request: Request) -> JSONResponse:
    state = request.app.state
    headers = request.headers
    tmp: Path | None = None
    try:
        now = state.clock()
        device_id = headers.get("x-device-id", "")
        meta = DeviceMeta.from_headers(headers)
        allow_unauth = state.settings.allow_unauthenticated_lan and is_lan_request(request)
        await run_in_threadpool(_authenticate, state, bearer_token(headers), device_id, meta,
                                now, allow_unauth)
        capture_id = headers.get("x-capture-id", "")
        if not naming.is_valid_capture_id(capture_id):
            raise Reject(400, "X-Capture-Id mancante o non valido")
        maximum = state.settings.max_upload_bytes
        length = _content_length(headers, maximum)
        tmp, size, sha = await _receive(request, state.archive, maximum)
        if size != length:
            raise Reject(400, f"ricevuti {size} byte su {length} dichiarati")
        status, body = await run_in_threadpool(
            _store, state, device_id, capture_id, headers.get("x-capture-ts"), meta,
            tmp, size, sha, now)
        return JSONResponse(body, status_code=status)
    except (Reject, AuthError) as exc:
        return _error(exc.status, exc.detail)
    except ClientDisconnect:
        log.warning("upload interrotto dal client")
        return _error(400, "upload interrotto")
    except OperationalError:
        log.exception("database non raggiungibile durante la ricezione")
        return _error(503, "database non disponibile")
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            log.error("disco pieno durante la ricezione")
            return _error(507, "spazio su disco esaurito")
        raise
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)  # già spostato se archiviato
```

- [ ] **Step 5: `app`**

`backend/secondbrain/app.py`:

```python
"""Applicazione FastAPI: stato condiviso, router, ciclo di vita (spec §5)."""
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import ingest
from .archive import Archive
from .catalog import make_engine, make_sessionmaker
from .clock import utcnow
from .config import Settings, load_settings
from .httputil import public_host

log = logging.getLogger(__name__)


def _is_device_path(path: str) -> bool:
    return path == "/captures" or path.startswith("/firmware/")


def create_app(settings: Settings, clock: Callable[[], datetime] = utcnow) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        removed = app.state.archive.clean_incoming()
        if removed:
            log.warning("rimossi %d upload incompleti da .incoming", removed)
        yield
        app.state.engine.dispose()

    app = FastAPI(title="secondbrain", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.clock = clock
    app.state.archive = Archive(settings.archive_dir)
    app.state.engine = make_engine(settings.database_url)
    app.state.sessionmaker = make_sessionmaker(app.state.engine)

    if settings.device_hostname:
        @app.middleware("http")
        async def device_host_guard(request: Request, call_next):
            if (public_host(request) == settings.device_hostname
                    and not _is_device_path(request.url.path)):
                return JSONResponse({"detail": "Not Found"}, status_code=404)
            return await call_next(request)

    app.include_router(ingest.router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    return app


def create_app_from_env() -> FastAPI:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return create_app(load_settings())
```

- [ ] **Step 6: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "backend: ricezione POST /captures con il contratto del firmware

Upload in streaming su .incoming con sha256 calcolato al volo, validazione WAV,
deduplicazione su (device, capture_id) confrontando il contenuto: stesso id e stesso
hash e' un ritentativo (409), stesso id con contenuto diverso e' il contatore unsynced
ripartito e si archivia. Se il commit sul DB fallisce i file appena scritti si
tolgono, cosi' il ritentativo del device non crea doppioni. Errori di disco e DB sono
5xx: il device tiene il file. Sull'hostname dei dispositivi rispondono solo le loro
route."
```

---

### Task 8: `rescan` — ricostruzione del catalogo dal disco

**Files:**
- Create: `backend/secondbrain/rescan.py`
- Modify: `backend/secondbrain/cli.py` (comando `rescan`)
- Test: `backend/tests/test_rescan.py`

**Interfaces:**
- Consumes: `Archive.iter_sidecar_rels`, `iter_wavs_without_sidecar`, `read_sidecar`, `abs`; `archive.TRASH`; `sidecar.sidecar_to_fields`; `naming.parse_day_dir`, `DEFAULT_DEVICE_TYPE`.
- Produces: `RescanReport(added: int, updated: int, removed: int, devices_created: list[str], problems: list[str])`, `rescan(s: Session, archive: Archive, now: datetime) -> RescanReport` (fa `flush`, non `commit`); comando `secondbrain rescan`.

- [ ] **Step 1: Test (falliscono)**

`backend/tests/test_rescan.py`:

```python
from datetime import date

import pytest
from sqlalchemy import delete, select

from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.models import Capture, Device
from secondbrain.rescan import rescan
from tests.helpers import DEV, DEV2, NOW, TEST_DB, make_wav, upload


@pytest.fixture
def archive(lan_client, settings):
    upload(lan_client, make_wav(fill=b"\x01\x00"))
    upload(lan_client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260924_080000",
           ts="2026-09-24T08:00:00Z", device=DEV2)
    return Archive(settings.archive_dir)


def snapshot(db):
    db.expire_all()
    return {(c.rel_path, c.sha256, c.title, c.day, c.trashed_at, c.device_id)
            for c in db.scalars(select(Capture))}


def first(db):
    db.expire_all()
    return db.scalars(select(Capture).where(Capture.device_id == DEV)).one()


def test_rebuilds_an_empty_catalog(archive, db):
    before = snapshot(db)
    db.execute(delete(Capture))
    db.execute(delete(Device))
    db.commit()
    report = rescan(db, archive, NOW)
    db.commit()
    assert (report.added, report.updated, report.removed) == (2, 0, 0)
    assert sorted(report.devices_created) == [DEV, DEV2]
    assert snapshot(db) == before


def test_nothing_to_do_on_a_consistent_catalog(archive, db):
    report = rescan(db, archive, NOW)
    assert (report.added, report.updated, report.removed, report.problems) == (0, 0, 0, [])


def test_follows_files_moved_by_hand(archive, db, settings):
    cap = first(db)
    src, dst = settings.archive_dir / "2026/09/23", settings.archive_dir / "2026/09/20"
    dst.mkdir(parents=True)
    for p in list(src.iterdir()):
        p.rename(dst / p.name)
    assert rescan(db, archive, NOW).updated == 1
    db.commit()
    db.refresh(cap)
    assert cap.rel_path.startswith("2026/09/20/") and cap.day == date(2026, 9, 20)


def test_picks_up_title_edited_in_the_sidecar(archive, db):
    cap = first(db)
    data = archive.read_sidecar(cap.rel_path)
    data["title"] = "scritto a mano"
    archive.write_sidecar(cap.rel_path, data)
    assert rescan(db, archive, NOW).updated == 1
    db.commit()
    db.refresh(cap)
    assert cap.title == "scritto a mano"


def test_removes_rows_whose_files_are_gone(archive, db):
    cap = first(db)
    archive.delete(cap.rel_path)
    assert rescan(db, archive, NOW).removed == 1
    db.commit()
    assert db.get(Capture, cap.id) is None


def test_trash_location_marks_trashed(archive, db):
    cap = first(db)
    new = archive.move(cap.rel_path, ".trash/2026/09/23")
    rescan(db, archive, NOW)
    db.commit()
    db.refresh(cap)
    assert (cap.rel_path, cap.trashed_at, cap.day) == (new, NOW, date(2026, 9, 23))


def test_reports_problems_and_goes_on(archive, db, settings):
    (settings.archive_dir / "2026/09/23/235959_70041dd8263c.wav").write_bytes(make_wav())
    broken = settings.archive_dir / "2026/09/22"
    broken.mkdir(parents=True)
    (broken / "000000_70041dd8263c.wav").write_bytes(make_wav())
    (broken / "000000_70041dd8263c.json").write_text("{non json")
    report = rescan(db, archive, NOW)
    assert any("235959" in p and "senza sidecar" in p for p in report.problems)
    assert any("000000" in p and "non leggibile" in p for p in report.problems)
    assert (report.added, report.updated) == (0, 0)


def test_cli_rescan(archive, db, monkeypatch, capsys, settings):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    db.execute(delete(Capture))
    db.commit()
    assert main(["rescan"]) == 0
    assert "aggiunte 2" in capsys.readouterr().out
```

Run: `uv run pytest tests/test_rescan.py -q`
Expected: FAIL, `ImportError` su `secondbrain.rescan`.

- [ ] **Step 2: Implementazione**

`backend/secondbrain/rescan.py`:

```python
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
```

In `backend/secondbrain/cli.py` aggiungere gli import `from .archive import Archive` e `from .rescan import rescan`, poi:

```python
def _rescan(args: argparse.Namespace) -> None:
    with open_session() as (s, settings):
        report = rescan(s, Archive(settings.archive_dir), utcnow())
        s.commit()
    print(f"Registrazioni: aggiunte {report.added}, aggiornate {report.updated}, "
          f"rimosse dal catalogo {report.removed}.")
    if report.devices_created:
        print("Dispositivi creati senza token: " + ", ".join(report.devices_created)
              + " (usa 'secondbrain device token <id>')")
    for problem in report.problems:
        print(f"attenzione: {problem}")


def _add_rescan_command(sub) -> None:
    sub.add_parser("rescan", help="ricostruisce il catalogo dall'archivio su disco") \
        .set_defaults(func=_rescan)
```

e aggiornare la tupla: `COMMAND_GROUPS = (_add_device_commands, _add_rescan_command)`.

- [ ] **Step 3: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/
git commit -m "backend: rescan ricostruisce il catalogo dai sidecar su disco

E' la garanzia che l'archivio basti a se stesso: dopo un trasloco su un altro NAS o
la perdita del DB si copia la cartella e si lancia rescan. Posizione dei file e
contenuto dei sidecar vincono sul DB, anche per spostamenti o titoli fatti a mano;
i file senza sidecar vengono segnalati e non importati a meta'."
```

---

### Task 9: OTA — release firmware, manifest, binari

**Files:**
- Modify: `backend/secondbrain/models.py` (`FirmwareRelease`)
- Create: `backend/migrations/versions/0002_firmware_releases.py`, `backend/secondbrain/ota.py`
- Modify: `backend/secondbrain/app.py` (include `ota.router`), `backend/secondbrain/cli.py` (gruppo `firmware`)
- Test: `backend/tests/test_ota.py`

**Interfaces:**
- Consumes: `devices.authenticate_any`, `devices.bearer_token`, `devices.AuthError`, `httputil.public_base_url`, `naming.is_valid_device_type`, `naming.DEFAULT_DEVICE_TYPE`.
- Produces: `models.FirmwareRelease(id, type, version, file, sha256, published_at, current)`; `ota.OtaError(ValueError)`, `ota.publish(s, firmware_dir: Path, src: Path, type_: str, version: str, now) -> FirmwareRelease`, `ota.rollback(s, type_) -> FirmwareRelease`, `ota.current_release(s, type_) -> FirmwareRelease | None`, `ota.list_releases(s) -> list[FirmwareRelease]`, `ota.router` (`GET /firmware/manifest.json`, `GET /firmware/{type_}/manifest.json`, `GET /firmware/{type_}/{filename}`); comandi `secondbrain firmware publish|rollback|list`.

- [ ] **Step 1: Modello e migrazione**

In `backend/secondbrain/models.py` aggiungere `UniqueConstraint` agli import di `sqlalchemy` e in fondo:

```python
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
```

`backend/migrations/versions/0002_firmware_releases.py`:

```python
"""release firmware

Revision ID: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "firmware_releases",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("file", sa.String(128), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("type", "version", name="uq_firmware_type_version"),
    )


def downgrade() -> None:
    op.drop_table("firmware_releases")
```

- [ ] **Step 2: Test (falliscono)**

`backend/tests/test_ota.py`:

```python
import hashlib

import pytest

from secondbrain.cli import main
from secondbrain.ota import OtaError, current_release, list_releases, publish, rollback
from tests.helpers import NOW, TEST_DB

FW = b"\xe9firmware-di-prova" * 100


@pytest.fixture
def fw_file(tmp_path):
    path = tmp_path / "secondbrain_fw.bin"
    path.write_bytes(FW)
    return path


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_publish(db, settings, fw_file):
    release = publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    assert release.current and release.file == "secondbrain-0.7.0.bin"
    assert (settings.firmware_dir / "epaper154" / release.file).read_bytes() == FW
    assert release.sha256 == hashlib.sha256(FW).hexdigest()


def test_new_release_becomes_the_only_current(db, settings, fw_file):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.1", NOW)
    assert current_release(db, "epaper154").version == "0.7.1"
    assert [(r.version, r.current) for r in list_releases(db)] == [("0.7.0", False), ("0.7.1", True)]


@pytest.mark.parametrize("type_,version", [("epaper154", "0.7"), ("epaper154", "v0.7.0"), ("../x", "0.7.0")])
def test_publish_validates(db, settings, fw_file, type_, version):
    with pytest.raises(OtaError):
        publish(db, settings.firmware_dir, fw_file, type_, version, NOW)


def test_publish_rejects_duplicates_and_missing_files(db, settings, fw_file, tmp_path):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    with pytest.raises(OtaError, match="già pubblicata"):
        publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    with pytest.raises(OtaError, match="non trovato"):
        publish(db, settings.firmware_dir, tmp_path / "manca.bin", "epaper154", "0.7.1", NOW)


def test_rollback(db, settings, fw_file):
    with pytest.raises(OtaError, match="nessuna release"):
        rollback(db, "epaper154")
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.1", NOW)
    assert rollback(db, "epaper154").version == "0.7.0"
    assert current_release(db, "epaper154").version == "0.7.0"
    with pytest.raises(OtaError, match="precedente"):
        rollback(db, "epaper154")


def test_manifest_404_without_release(client, token):
    assert client.get("/firmware/manifest.json", headers=auth(token)).status_code == 404


def test_manifest_and_binary(client, db, settings, fw_file, token):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    r = client.get("/firmware/manifest.json", headers=auth(token))
    assert r.status_code == 200
    assert r.json() == {
        "version": "0.7.0",
        "url": "http://testserver/firmware/epaper154/secondbrain-0.7.0.bin",
        "sha256": hashlib.sha256(FW).hexdigest(),
    }
    assert client.get("/firmware/epaper154/manifest.json", headers=auth(token)).json() == r.json()
    binary = client.get("/firmware/epaper154/secondbrain-0.7.0.bin", headers=auth(token))
    assert binary.status_code == 200 and binary.content == FW


def test_manifest_url_behind_the_tunnel(client, db, settings, fw_file, token):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    headers = auth(token) | {"Host": "ingest.example.org", "X-Forwarded-Proto": "https"}
    url = client.get("/firmware/manifest.json", headers=headers).json()["url"]
    assert url == "https://ingest.example.org/firmware/epaper154/secondbrain-0.7.0.bin"


@pytest.mark.parametrize("path", [
    "/firmware/epaper154/secondbrain-9.9.9.bin",
    "/firmware/epaper154/..%2F..%2Fetc%2Fpasswd",
    "/firmware/..%2F..%2Fx/secondbrain-0.7.0.bin",
    "/firmware/epaper154/manifest.bin",
])
def test_binary_not_found(client, db, settings, fw_file, token, path):
    publish(db, settings.firmware_dir, fw_file, "epaper154", "0.7.0", NOW)
    db.commit()
    assert client.get(path, headers=auth(token)).status_code == 404


def test_firmware_requires_token(client):
    assert client.get("/firmware/manifest.json").status_code == 401


def test_firmware_open_in_lan_mode(lan_client):
    assert lan_client.get("/firmware/manifest.json").status_code == 404  # nessuna release
    tunnel = {"Cf-Connecting-Ip": "203.0.113.7"}
    assert lan_client.get("/firmware/manifest.json", headers=tunnel).status_code == 401


def test_cli_publish_list_rollback(monkeypatch, db, settings, fw_file, capsys):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("FIRMWARE_DIR", str(settings.firmware_dir))
    assert main(["firmware", "publish", str(fw_file), "--version", "0.7.0"]) == 0
    assert main(["firmware", "publish", str(fw_file), "--version", "0.7.1"]) == 0
    assert main(["firmware", "rollback"]) == 0
    assert "0.7.0" in capsys.readouterr().out
    assert main(["firmware", "list"]) == 0
    out = capsys.readouterr().out
    assert "* epaper154 0.7.0" in out and "  epaper154 0.7.1" in out
    assert main(["firmware", "publish", str(fw_file), "--version", "0.7.0"]) == 1
```

Run: `uv run pytest tests/test_ota.py -q`
Expected: FAIL, `ImportError` su `secondbrain.ota`.

- [ ] **Step 3: Implementazione**

`backend/secondbrain/ota.py`:

```python
"""OTA: release firmware, manifest e binari per tipo di dispositivo (spec §9).

Pubblicare è sempre un comando esplicito: il manifest si genera dalla release corrente,
non esiste un file manifest da dimenticare con una versione sbagliata.
"""
import hashlib
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .devices import AuthError, authenticate_any, bearer_token
from .httputil import is_lan_request, public_base_url
from .models import FirmwareRelease
from .naming import DEFAULT_DEVICE_TYPE, is_valid_device_type

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
FILE_RE = re.compile(r"secondbrain-\d+\.\d+\.\d+\.bin")


class OtaError(ValueError):
    """Operazione non valida sulle release firmware."""


def release_filename(version: str) -> str:
    return f"secondbrain-{version}.bin"


def current_release(s: Session, type_: str) -> FirmwareRelease | None:
    return s.scalar(select(FirmwareRelease)
                    .where(FirmwareRelease.type == type_, FirmwareRelease.current.is_(True)))


def list_releases(s: Session) -> list[FirmwareRelease]:
    return list(s.scalars(select(FirmwareRelease)
                          .order_by(FirmwareRelease.type, FirmwareRelease.id)))


def publish(s: Session, firmware_dir: Path, src: Path, type_: str, version: str,
            now: datetime) -> FirmwareRelease:
    if not is_valid_device_type(type_):
        raise OtaError(f"tipo di dispositivo non valido: {type_!r}")
    if VERSION_RE.fullmatch(version) is None:
        raise OtaError(f"versione non nel formato X.Y.Z: {version!r}")
    existing = s.scalar(select(FirmwareRelease).where(
        FirmwareRelease.type == type_, FirmwareRelease.version == version))
    if existing is not None:
        raise OtaError(f"{type_} {version} già pubblicata")
    if not src.is_file():
        raise OtaError(f"file non trovato: {src}")
    dest_dir = firmware_dir / type_
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / release_filename(version)
    tmp = dest.with_name(dest.name + ".tmp")
    shutil.copyfile(src, tmp)
    os.replace(tmp, dest)
    with open(dest, "rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    s.execute(update(FirmwareRelease).where(FirmwareRelease.type == type_)
              .values(current=False))
    release = FirmwareRelease(type=type_, version=version, file=dest.name, sha256=sha,
                              published_at=now, current=True)
    s.add(release)
    s.flush()
    return release


def rollback(s: Session, type_: str) -> FirmwareRelease:
    current = current_release(s, type_)
    if current is None:
        raise OtaError(f"nessuna release corrente per {type_}")
    previous = s.scalar(select(FirmwareRelease)
                        .where(FirmwareRelease.type == type_, FirmwareRelease.id < current.id)
                        .order_by(FirmwareRelease.id.desc()).limit(1))
    if previous is None:
        raise OtaError(f"nessuna release precedente a {current.version}")
    current.current = False
    s.flush()
    previous.current = True
    s.flush()
    return previous


router = APIRouter()


def require_device(request: Request) -> None:
    state = request.app.state
    with state.sessionmaker() as s:
        try:
            authenticate_any(s, bearer_token(request.headers),
                             state.settings.allow_unauthenticated_lan and is_lan_request(request))
        except AuthError as exc:
            raise HTTPException(exc.status, exc.detail) from None


def _manifest(request: Request, type_: str) -> dict:
    if not is_valid_device_type(type_):
        raise HTTPException(404, "tipo sconosciuto")
    with request.app.state.sessionmaker() as s:
        release = current_release(s, type_)
    if release is None:
        raise HTTPException(404, "nessuna release pubblicata")
    return {
        "version": release.version,
        "url": f"{public_base_url(request)}/firmware/{release.type}/{release.file}",
        "sha256": release.sha256,
    }


@router.get("/firmware/manifest.json", dependencies=[Depends(require_device)])
def default_manifest(request: Request) -> dict:
    return _manifest(request, DEFAULT_DEVICE_TYPE)


@router.get("/firmware/{type_}/manifest.json", dependencies=[Depends(require_device)])
def manifest(request: Request, type_: str) -> dict:
    return _manifest(request, type_)


@router.get("/firmware/{type_}/{filename}", dependencies=[Depends(require_device)])
def binary(request: Request, type_: str, filename: str) -> FileResponse:
    if not is_valid_device_type(type_) or FILE_RE.fullmatch(filename) is None:
        raise HTTPException(404, "file sconosciuto")
    with request.app.state.sessionmaker() as s:
        release = s.scalar(select(FirmwareRelease).where(
            FirmwareRelease.type == type_, FirmwareRelease.file == filename))
    path = request.app.state.settings.firmware_dir / type_ / filename
    if release is None or not path.is_file():
        raise HTTPException(404, "file sconosciuto")
    return FileResponse(path, media_type="application/octet-stream")
```

In `backend/secondbrain/app.py`: `from . import ingest, ota` e, dopo `app.include_router(ingest.router)`, `app.include_router(ota.router)`.

In `backend/secondbrain/cli.py` aggiungere `from pathlib import Path` e `from . import ota`, poi:

```python
def _firmware_publish(args: argparse.Namespace) -> None:
    with open_session() as (s, settings):
        try:
            release = ota.publish(s, settings.firmware_dir, Path(args.file), args.type,
                                  args.version, utcnow())
        except ota.OtaError as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print(f"Pubblicata {release.type} {release.version} (sha256 {release.sha256}).")


def _firmware_rollback(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        try:
            release = ota.rollback(s, args.type)
        except ota.OtaError as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print(f"Release corrente per {release.type}: {release.version}.")


def _firmware_list(args: argparse.Namespace) -> None:
    with open_session() as (s, _):
        for r in ota.list_releases(s):
            mark = "*" if r.current else " "
            print(f"{mark} {r.type} {r.version}  {r.published_at.isoformat(timespec='seconds')}"
                  f"  {r.sha256[:12]}")


def _add_firmware_commands(sub) -> None:
    group = sub.add_parser("firmware", help="release firmware per l'OTA")
    actions = group.add_subparsers(dest="action", required=True)
    pub = actions.add_parser("publish", help="pubblica un binario come release corrente")
    pub.add_argument("file")
    pub.add_argument("--version", required=True, help="X.Y.Z, come version.txt del firmware")
    pub.add_argument("--type", default=DEFAULT_DEVICE_TYPE)
    pub.set_defaults(func=_firmware_publish)
    back = actions.add_parser("rollback", help="torna alla release precedente")
    back.add_argument("--type", default=DEFAULT_DEVICE_TYPE)
    back.set_defaults(func=_firmware_rollback)
    actions.add_parser("list", help="elenca le release (* = corrente)") \
        .set_defaults(func=_firmware_list)
```

e `COMMAND_GROUPS = (_add_device_commands, _add_rescan_command, _add_firmware_commands)`.

- [ ] **Step 4: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS, compreso `test_migrations_match_models` con la nuova tabella.

- [ ] **Step 5: Commit**

```bash
git add backend/
git commit -m "backend: OTA servito dal backend con release pubblicate da CLI

Il manifest si genera dalla release corrente con l'host della richiesta, cosi' lo
stesso binario si scarica in LAN e dal tunnel. Pubblicare e tornare indietro sono
comandi espliciti: sparisce la trappola del manifest dimenticato con una versione
piu' alta che aggiornava il device al primo ciclo. /firmware/manifest.json resta
come alias per il firmware attuale."
```

---
### Task 10: Login, sessioni e CSRF

**Files:**
- Modify: `backend/secondbrain/models.py` (`User`, `WebSession`, `LoginAttempt`)
- Create: `backend/migrations/versions/0003_web_auth.py`
- Create: `backend/secondbrain/web/__init__.py`, `web/templating.py`, `web/auth.py`, `web/deps.py`, `web/login.py`
- Create: `backend/secondbrain/web/templates/base.html`, `web/templates/login.html`, `web/static/style.css`, `web/static/htmx.min.js`
- Modify: `backend/secondbrain/app.py` (static, router login, handler), `backend/secondbrain/cli.py` (`set-password`), `backend/tests/helpers.py` (`PASSWORD`)
- Test: `backend/tests/test_web_auth.py`

**Interfaces:**
- Consumes: `httputil.public_scheme`, `cli.open_session`.
- Produces: `models.User(id, password_hash, updated_at)`, `models.WebSession(id, csrf_token, created_at, expires_at)`, `models.LoginAttempt(id, at, ok, ip)`; `web.auth.COOKIE_NAME = "sb_session"`, `SESSION_LIFETIME`, `MAX_FAILURES`, `FAILURE_WINDOW`, eccezioni `NotAuthenticated`, `LockedOut`, `CsrfError`, `BadPassword(ValueError)`; `set_password(s, password, now)` (flush, chiude tutte le sessioni), `login(s, password, now, ip) -> WebSession` (fa commit), `current_session(s, sid, now) -> WebSession | None`, `logout(s, sid)`; `web.deps.get_db(request)`, `require_login(request, db) -> WebSession`, `require_csrf(request, ws) -> WebSession` (header `X-CSRF-Token` o campo form `csrf`); `web.templating.templates`, `STATIC_DIR`, `MONTHS`, `format_duration(seconds) -> str`, `format_size(n) -> str`; route `GET /login`, `POST /login`, `POST /logout`, `GET /` (→ `/browse`); comando `secondbrain set-password [--stdin]`.

- [ ] **Step 1: Modelli e migrazione**

In `backend/secondbrain/models.py` in fondo:

```python
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

`backend/migrations/versions/0003_web_auth.py`:

```python
"""utente, sessioni web, tentativi di login

Revision ID: 0003
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "web_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("csrf_token", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("ip", sa.String(64), nullable=True),
    )
    op.create_index("ix_login_attempts_at", "login_attempts", ["at"])


def downgrade() -> None:
    op.drop_table("login_attempts")
    op.drop_table("web_sessions")
    op.drop_table("users")
```

In `backend/tests/helpers.py` aggiungere `PASSWORD = "una-password-lunga"`.

- [ ] **Step 2: Test (falliscono)**

`backend/tests/test_web_auth.py`:

```python
import io
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from secondbrain.cli import main
from secondbrain.models import LoginAttempt, WebSession
from secondbrain.web.auth import (SESSION_LIFETIME, BadPassword, LockedOut, NotAuthenticated,
                                  current_session, login, set_password)
from secondbrain.web.templating import format_duration, format_size
from tests.helpers import NOW, PASSWORD, TEST_DB


@pytest.fixture
def user(db):
    set_password(db, PASSWORD, NOW)
    db.commit()


def failures(db):
    return db.scalar(select(func.count()).select_from(LoginAttempt)
                     .where(LoginAttempt.ok.is_(False)))


def test_password_minimum_length(db):
    with pytest.raises(BadPassword):
        set_password(db, "corta", NOW)


def test_login_ok_and_wrong(db, user):
    ws = login(db, PASSWORD, NOW, "1.2.3.4")
    assert current_session(db, ws.id, NOW) is ws
    assert current_session(db, ws.id, NOW + SESSION_LIFETIME) is None
    with pytest.raises(NotAuthenticated):
        login(db, "sbagliata", NOW, None)
    assert failures(db) == 1


def test_login_without_any_user(db):
    with pytest.raises(NotAuthenticated):
        login(db, PASSWORD, NOW, None)


def test_lockout_after_five_failures(db, user):
    for _ in range(5):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, None)
    with pytest.raises(LockedOut):
        login(db, PASSWORD, NOW, None)  # bloccato anche con la password giusta
    assert login(db, PASSWORD, NOW + timedelta(minutes=16), None)


def test_success_clears_failures(db, user):
    for _ in range(4):
        with pytest.raises(NotAuthenticated):
            login(db, "sbagliata", NOW, None)
    login(db, PASSWORD, NOW, None)
    assert failures(db) == 0


def test_new_password_closes_open_sessions(db, user):
    ws = login(db, PASSWORD, NOW, None)
    set_password(db, "un'altra-password-lunga", NOW)
    db.commit()
    db.expire_all()
    assert current_session(db, ws.id, NOW) is None


def test_login_page(client):
    r = client.get("/login")
    assert r.status_code == 200 and 'name="password"' in r.text


def test_wrong_password_page(client, user):
    r = client.post("/login", data={"password": "no"})
    assert r.status_code == 401 and "Password errata" in r.text


def test_login_sets_a_safe_cookie(client, user):
    r = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/browse"
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" not in cookie


def test_cookie_is_secure_behind_the_tunnel(client, user):
    r = client.post("/login", data={"password": PASSWORD},
                    headers={"X-Forwarded-Proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


def test_lockout_page(client, user):
    for _ in range(5):
        client.post("/login", data={"password": "no"})
    r = client.post("/login", data={"password": PASSWORD})
    assert r.status_code == 429 and "Troppi tentativi" in r.text


def test_pages_require_login(client, user):
    r = client.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")
    r = client.get("/", headers={"HX-Request": "true"}, follow_redirects=False)
    assert (r.status_code, r.headers["hx-redirect"]) == (401, "/login")
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    r = client.get("/", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/browse")


def test_logout_requires_csrf(client, user, db):
    client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert client.post("/logout", follow_redirects=False).status_code == 403
    ws = db.scalars(select(WebSession)).one()
    r = client.post("/logout", data={"csrf": ws.csrf_token}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")
    db.expire_all()
    assert db.scalars(select(WebSession)).all() == []


def test_cli_set_password(monkeypatch, db, capsys):
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert main(["set-password", "--stdin"]) == 0
    assert login(db, PASSWORD, NOW, None)
    monkeypatch.setattr("sys.stdin", io.StringIO("corta\n"))
    assert main(["set-password", "--stdin"]) == 1


@pytest.mark.parametrize("seconds,text", [(0, "0:00"), (59.6, "1:00"), (125, "2:05"), (600, "10:00")])
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize("size,text", [(10, "1 KB"), (32044, "31 KB"), (int(19.4 * 1024 * 1024), "19,4 MB")])
def test_format_size(size, text):
    assert format_size(size) == text
```

Run: `uv run pytest tests/test_web_auth.py -q`
Expected: FAIL, `ImportError` su `secondbrain.web`.

- [ ] **Step 3: Autenticazione e dipendenze**

`backend/secondbrain/web/__init__.py`:

```python
"""Interfaccia web: finder, login, azioni (spec §7)."""
```

`backend/secondbrain/web/auth.py`:

```python
"""Login a utente singolo, sessioni in DB, blocco dopo tentativi falliti (spec §7)."""
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..models import LoginAttempt, User, WebSession

COOKIE_NAME = "sb_session"
SESSION_LIFETIME = timedelta(days=30)
MAX_FAILURES = 5
FAILURE_WINDOW = timedelta(minutes=15)
MIN_PASSWORD_LEN = 10
TOKEN_BYTES = 32
USER_ID = 1

_hasher = PasswordHasher()


class NotAuthenticated(Exception):
    """Serve il login."""


class LockedOut(Exception):
    """Troppi tentativi falliti di recente."""


class CsrfError(Exception):
    """Token CSRF mancante o diverso da quello della sessione."""


class BadPassword(ValueError):
    """Password non accettabile."""


def set_password(s: Session, password: str, now: datetime) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise BadPassword(f"password troppo corta: almeno {MIN_PASSWORD_LEN} caratteri")
    hashed = _hasher.hash(password)
    user = s.get(User, USER_ID)
    if user is None:
        s.add(User(id=USER_ID, password_hash=hashed, updated_at=now))
    else:
        user.password_hash = hashed
        user.updated_at = now
    s.execute(delete(WebSession))  # chi era entrato con la vecchia password esce
    s.flush()


def _recent_failures(s: Session, now: datetime) -> int:
    return s.scalar(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.ok.is_(False), LoginAttempt.at > now - FAILURE_WINDOW))


def _verify(user: User | None, password: str) -> bool:
    if user is None:
        return False
    try:
        return _hasher.verify(user.password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def login(s: Session, password: str, now: datetime, ip: str | None) -> WebSession:
    """Fa commit: il tentativo fallito deve restare registrato anche quando si solleva."""
    if _recent_failures(s, now) >= MAX_FAILURES:
        raise LockedOut
    ok = _verify(s.get(User, USER_ID), password)
    s.add(LoginAttempt(at=now, ok=ok, ip=ip[:64] if ip else None))
    if not ok:
        s.commit()
        raise NotAuthenticated
    s.execute(delete(LoginAttempt).where(LoginAttempt.ok.is_(False)))
    s.execute(delete(WebSession).where(WebSession.expires_at <= now))
    session = WebSession(id=secrets.token_urlsafe(TOKEN_BYTES),
                         csrf_token=secrets.token_urlsafe(TOKEN_BYTES),
                         created_at=now, expires_at=now + SESSION_LIFETIME)
    s.add(session)
    s.commit()
    return session


def current_session(s: Session, sid: str, now: datetime) -> WebSession | None:
    session = s.get(WebSession, sid)
    if session is None or session.expires_at <= now:
        return None
    return session


def logout(s: Session, sid: str) -> None:
    s.execute(delete(WebSession).where(WebSession.id == sid))
    s.commit()
```

`backend/secondbrain/web/deps.py`:

```python
"""Dipendenze FastAPI delle pagine: sessione DB, login, CSRF."""
import secrets
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from ..models import WebSession
from .auth import COOKIE_NAME, CsrfError, NotAuthenticated, current_session


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as s:
        yield s


def require_login(request: Request, db: Session = Depends(get_db)) -> WebSession:
    sid = request.cookies.get(COOKIE_NAME)
    session = current_session(db, sid, request.app.state.clock()) if sid else None
    if session is None:
        raise NotAuthenticated
    return session


async def require_csrf(request: Request,
                       session: WebSession = Depends(require_login)) -> WebSession:
    token = request.headers.get("x-csrf-token")
    if token is None:
        token = (await request.form()).get("csrf")
    if not isinstance(token, str) or not secrets.compare_digest(token, session.csrf_token):
        raise CsrfError
    return session
```

- [ ] **Step 4: Template, stile, htmx**

`backend/secondbrain/web/templating.py`:

```python
"""Jinja: template, filtri di formato, nomi dei mesi."""
from pathlib import Path

from fastapi.templating import Jinja2Templates

WEB_DIR = Path(__file__).parent
STATIC_DIR = WEB_DIR / "static"
MONTHS = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio",
          "agosto", "settembre", "ottobre", "novembre", "dicembre")
KIB = 1024
MIB = 1024 * 1024


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def format_size(size: int) -> str:
    if size < MIB:
        return f"{max(1, round(size / KIB))} KB"
    return f"{size / MIB:.1f} MB".replace(".", ",")


templates = Jinja2Templates(directory=WEB_DIR / "templates")
templates.env.filters["duration"] = format_duration
templates.env.filters["size"] = format_size
templates.env.globals["MONTHS"] = MONTHS
```

`backend/secondbrain/web/templates/base.html`:

```html
<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{% block title %}Second Brain{% endblock %}</title>
<link rel="stylesheet" href="/static/style.css">
<script src="/static/htmx.min.js" defer></script>
</head>
<body{% if csrf %} hx-headers='{"X-CSRF-Token": "{{ csrf }}"}'{% endif %}>
{% block body %}{% endblock %}
</body>
</html>
```

`backend/secondbrain/web/templates/login.html`:

```html
{% extends "base.html" %}
{% block title %}Accesso · Second Brain{% endblock %}
{% block body %}
<main class="login">
  <h1>Second Brain</h1>
  <form method="post" action="/login">
    <label for="password">Password</label>
    <input id="password" name="password" type="password" autocomplete="current-password" autofocus required>
    {% if error %}<p class="error">{{ error }}</p>{% endif %}
    <button type="submit">Entra</button>
  </form>
</main>
{% endblock %}
```

`backend/secondbrain/web/static/style.css` (contiene anche le classi del finder usate nei Task 11–12):

```css
:root { --bg: #fafaf8; --fg: #1d1d1b; --muted: #6b6b66; --line: #e2e2dc; --accent: #2f5d8a; --warn: #9a5b00; --warn-bg: #fff3dc; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --fg: #ececea; --muted: #a0a09a; --line: #33332f; --accent: #8db8e4; --warn: #f0b35a; --warn-bg: #3a2a10; }
}
* { box-sizing: border-box; }
body { margin: 0; font: 15px/1.45 system-ui, sans-serif; background: var(--bg); color: var(--fg); }
a { color: var(--accent); text-decoration: none; }
button, input, select { font: inherit; color: inherit; }
button { cursor: pointer; background: none; border: 1px solid var(--line); border-radius: 6px; padding: .3rem .7rem; }
input, select { border: 1px solid var(--line); border-radius: 6px; padding: .3rem .5rem; background: var(--bg); }
h1 { font-size: 1.2rem; }
.login { max-width: 22rem; margin: 15vh auto; padding: 0 1rem; }
.login input { width: 100%; margin: .5rem 0; }
.error { color: var(--warn); }
.empty { color: var(--muted); }
.count { color: var(--muted); font-size: .85em; }
.mono { font-family: ui-monospace, monospace; font-size: .85em; }
.badge { background: var(--warn-bg); color: var(--warn); border-radius: 4px; padding: 0 .4rem; font-size: .85em; }
.top { display: flex; flex-wrap: wrap; gap: .5rem 1rem; align-items: center; padding: .6rem 1rem; border-bottom: 1px solid var(--line); }
.top .brand { font-weight: 600; color: var(--fg); margin-right: auto; }
.top nav { display: flex; flex-wrap: wrap; gap: .8rem; align-items: center; }
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
@media (max-width: 700px) {
  .layout { display: block; }
  .tree { width: auto; border-right: 0; border-bottom: 1px solid var(--line); }
  .tree-toggle { display: inline-block; }
  .tree > ul { display: none; }
  #tree-toggle:checked ~ ul { display: block; }
  .row .meta { margin-left: 0; width: 100%; }
}
```

Scaricare htmx (file statico versionato nel repo, nessun build):

Run: `curl -fsSL -o backend/secondbrain/web/static/htmx.min.js https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js && head -c 60 backend/secondbrain/web/static/htmx.min.js`
Expected: inizio di un file JavaScript minificato (circa 50 KB in tutto).

- [ ] **Step 5: Route di login e integrazione nell'app**

`backend/secondbrain/web/login.py`:

```python
"""Pagine di accesso e uscita."""
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from ..httputil import public_scheme
from ..models import WebSession
from .auth import COOKIE_NAME, SESSION_LIFETIME, LockedOut, NotAuthenticated, login, logout
from .deps import get_db, require_csrf, require_login
from .templating import templates

router = APIRouter()


def _client_ip(request: Request) -> str | None:
    return request.headers.get("cf-connecting-ip") or (request.client.host if request.client else None)


@router.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(request: Request, password: str = Form(""), db: Session = Depends(get_db)):
    try:
        session = login(db, password, request.app.state.clock(), _client_ip(request))
    except LockedOut:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Troppi tentativi falliti: riprova tra qualche minuto."}, status_code=429)
    except NotAuthenticated:
        return templates.TemplateResponse(request, "login.html", {"error": "Password errata."},
                                          status_code=401)
    response = RedirectResponse("/browse", status_code=303)
    response.set_cookie(COOKIE_NAME, session.id, max_age=int(SESSION_LIFETIME.total_seconds()),
                        httponly=True, samesite="lax",
                        secure=public_scheme(request) == "https", path="/")
    return response


@router.post("/logout")
def logout_submit(session: WebSession = Depends(require_csrf), db: Session = Depends(get_db)):
    logout(db, session.id)
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/")
def home(session: WebSession = Depends(require_login)):
    return RedirectResponse("/browse", status_code=303)
```

In `backend/secondbrain/app.py`:
- import: `from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response`, `from fastapi.staticfiles import StaticFiles`, `from .web import login as web_login`, `from .web.auth import CsrfError, NotAuthenticated`, `from .web.templating import STATIC_DIR`;
- dopo `app.include_router(ota.router)`:

```python
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(web_login.router)

    @app.exception_handler(NotAuthenticated)
    async def login_required(request: Request, exc: NotAuthenticated) -> Response:
        if request.headers.get("hx-request"):
            return Response(status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(CsrfError)
    async def csrf_failed(request: Request, exc: CsrfError) -> Response:
        return PlainTextResponse("token CSRF mancante o non valido", status_code=403)
```

In `backend/secondbrain/cli.py` aggiungere `import getpass` e `from .web.auth import BadPassword, set_password`, poi:

```python
def _set_password(args: argparse.Namespace) -> None:
    if args.stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Nuova password: ")
        if getpass.getpass("Ripeti la password: ") != password:
            raise CliError("le due password non coincidono")
    with open_session() as (s, _):
        try:
            set_password(s, password, utcnow())
        except BadPassword as exc:
            raise CliError(str(exc)) from None
        s.commit()
    print("Password impostata; le sessioni aperte sono state chiuse.")


def _add_password_command(sub) -> None:
    cmd = sub.add_parser("set-password", help="imposta la password dell'interfaccia web")
    cmd.add_argument("--stdin", action="store_true", help="legge la password da stdin")
    cmd.set_defaults(func=_set_password)
```

e aggiungere `_add_password_command` in fondo a `COMMAND_GROUPS`.

- [ ] **Step 6: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "backend: login a utente singolo con sessioni in DB, CSRF e blocco dei tentativi

La UI sara' raggiungibile da internet attraverso il tunnel: password con argon2,
cookie HttpOnly e SameSite, Secure quando si arriva in HTTPS, token CSRF su ogni
azione e blocco di 15 minuti dopo 5 tentativi falliti. Le sessioni stanno in DB con
id casuali, niente segreto di firma da gestire; cambiare password le chiude tutte."
```

---

### Task 11: Finder — navigazione, dettaglio, audio

**Files:**
- Modify: `backend/secondbrain/catalog.py` (query di navigazione)
- Create: `backend/secondbrain/web/context.py`, `backend/secondbrain/web/browse.py`
- Create: `backend/secondbrain/web/templates/finder.html`, `grid.html`, `day.html`, `_row.html`, `_detail.html`
- Modify: `backend/secondbrain/app.py` (router), `backend/tests/conftest.py` (fixture `ui`, `recordings`), `backend/tests/helpers.py` (`capture_by`)
- Test: `backend/tests/test_browse.py`

**Interfaces:**
- Consumes: `web.deps.get_db`, `require_login`, `web.templating.templates`, `MONTHS`, `Archive.related_files`, `Archive.abs`.
- Produces: in `catalog`: `year_counts(s, device_id=None) -> list[tuple[int, int]]` (anni decrescenti), `month_counts(s, year, device_id=None)`, `day_counts(s, year, month, device_id=None)` (crescenti), `list_day(s, day, device_id=None) -> list[Capture]` (per `recorded_at`), `count_estimated(s) -> int`, `list_estimated(s) -> list[Capture]`, `list_trashed(s) -> list[Capture]`; tutte escludono il cestino tranne `list_trashed`. In `web.context`: `clean_device(value) -> str | None`, `base_context(request, db, session, device=None) -> dict` (chiavi `csrf`, `tz`, `local`, `device`, `devices`, `device_names`, `qs`, `estimated_count`), `page_context(request, db, session, device=None, year=None, month=None) -> dict` (in più `year`, `month`, `tree`). Route: `GET /browse`, `/browse/{year}`, `/browse/{year}/{month}`, `/browse/{year}/{month}/{day}`, `GET /captures/{id}` (frammento), `/captures/{id}/audio`, `/captures/{id}/files/{name}`. Template `_row.html` (usa `c`, `tz`, `device_names`, `show_day` opzionale) e `_detail.html` (usa `c`, `files`, `local`, `device_names`). Fixture `ui` (`Ui(client, csrf)`: `lan_client` con login fatto) e `recordings` (tre catture caricate, restituisce `ui`); helper `capture_by(db, capture_id) -> Capture`.

- [ ] **Step 1: Fixture e helper**

Aggiungere a `backend/tests/helpers.py`:

```python
from sqlalchemy import select


def capture_by(db, capture_id: str) -> Capture:
    db.expire_all()
    return db.scalars(select(Capture).where(Capture.capture_id == capture_id)).one()
```

Aggiungere a `backend/tests/conftest.py` (import: `from dataclasses import dataclass`, `from sqlalchemy import select`, `from secondbrain.models import WebSession`, `from secondbrain.web.auth import set_password`, e da `tests.helpers` anche `DEV2`, `PASSWORD`, `make_wav`, `upload`):

```python
@dataclass
class Ui:
    client: TestClient
    csrf: str


@pytest.fixture
def ui(lan_client, db):
    set_password(db, PASSWORD, NOW)
    db.commit()
    lan_client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    session = db.scalars(select(WebSession)).one()
    return Ui(lan_client, session.csrf_token)


@pytest.fixture
def recordings(ui):
    """23/09 21:15:30 (DEV), 23/09 10:00:00 (DEV2), 10/08 09:00:00 (DEV), ora di Roma."""
    upload(ui.client, make_wav(fill=b"\x01\x00"))
    upload(ui.client, make_wav(fill=b"\x02\x00"), capture_id="cap_20260923_080000",
           ts="2026-09-23T08:00:00Z", device=DEV2)
    upload(ui.client, make_wav(fill=b"\x03\x00"), capture_id="cap_20260810_070000",
           ts="2026-08-10T07:00:00Z")
    return ui
```

- [ ] **Step 2: Test (falliscono)**

`backend/tests/test_browse.py`:

```python
import uuid

import pytest

from tests.helpers import DEV2, NOW, capture_by


def get(ui, url, **kw):
    return ui.client.get(url, **kw)


def test_requires_login(client):
    r = client.get("/browse", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/login")


def test_root_lists_years(recordings):
    r = get(recordings, "/browse")
    assert r.status_code == 200
    assert 'href="/browse/2026"' in r.text and '<span class="count">3</span>' in r.text


def test_year_lists_months(recordings):
    r = get(recordings, "/browse/2026")
    assert "agosto" in r.text and "settembre" in r.text
    assert 'href="/browse/2026/09"' in r.text


def test_month_lists_days(recordings):
    r = get(recordings, "/browse/2026/09")
    assert 'href="/browse/2026/09/23"' in r.text and '<span class="count">2</span>' in r.text


def test_day_lists_recordings_in_time_order(recordings):
    text = get(recordings, "/browse/2026/09/23").text
    assert text.index("10:00:00") < text.index("21:15:30")
    assert "senza titolo" in text


def test_device_filter(recordings):
    text = get(recordings, f"/browse/2026/09/23?device={DEV2}").text
    assert "10:00:00" in text and "21:15:30" not in text
    assert f'href="/browse/2026/09?device={DEV2}"' in text
    both = get(recordings, "/browse/2026/09/23?device=../x").text
    assert "10:00:00" in both and "21:15:30" in both


def test_trashed_recordings_are_hidden(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    cap.trashed_at = NOW
    db.commit()
    assert "21:15:30" not in get(recordings, "/browse/2026/09/23").text
    assert '<span class="count">2</span>' in get(recordings, "/browse").text


@pytest.mark.parametrize("url,status", [("/browse/2026/02/30", 404), ("/browse/2026/13", 422)])
def test_invalid_dates(recordings, url, status):
    assert get(recordings, url).status_code == status


def test_detail_lists_related_files(recordings, db, settings):
    cap = capture_by(db, "cap_20260923_191530")
    (settings.archive_dir / cap.rel_path).with_suffix(".transcript.md").write_text("ciao")
    r = get(recordings, f"/captures/{cap.id}")
    assert r.status_code == 200
    for name in ["211530_70041dd8263c.json", "211530_70041dd8263c.transcript.md",
                 "211530_70041dd8263c.wav"]:
        assert name in r.text
    assert f'src="/captures/{cap.id}/audio"' in r.text


def test_audio_supports_range(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    r = get(recordings, f"/captures/{cap.id}/audio", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    assert r.headers["content-type"].startswith("audio/wav")


def test_file_download(recordings, db):
    cap = capture_by(db, "cap_20260923_191530")
    r = get(recordings, f"/captures/{cap.id}/files/211530_70041dd8263c.json")
    assert r.status_code == 200 and r.json()["capture_id"] == "cap_20260923_191530"
    assert "attachment" in r.headers["content-disposition"]


@pytest.mark.parametrize("name", ["..%2F..%2F..%2Fetc%2Fpasswd", "100000_aabbccddeeff.wav", "x.json"])
def test_file_route_serves_only_related_files(recordings, db, name):
    cap = capture_by(db, "cap_20260923_191530")
    assert get(recordings, f"/captures/{cap.id}/files/{name}").status_code == 404


def test_unknown_capture(recordings):
    assert get(recordings, f"/captures/{uuid.uuid4()}").status_code == 404
```

Run: `uv run pytest tests/test_browse.py -q`
Expected: FAIL (le route `/browse` rispondono 404).

- [ ] **Step 3: Query di navigazione**

Aggiungere a `backend/secondbrain/catalog.py` (import: `from datetime import date`, `from sqlalchemy import Integer, cast, extract, func`):

```python
_YEAR = cast(extract("year", Capture.day), Integer)
_MONTH = cast(extract("month", Capture.day), Integer)
_DAY = cast(extract("day", Capture.day), Integer)


def _active(stmt, device_id: str | None):
    stmt = stmt.where(Capture.trashed_at.is_(None))
    return stmt.where(Capture.device_id == device_id) if device_id else stmt


def _counts(s: Session, stmt) -> list[tuple[int, int]]:
    return [(int(key), int(n)) for key, n in s.execute(stmt)]


def year_counts(s: Session, device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = select(_YEAR, func.count()).group_by(_YEAR).order_by(_YEAR.desc())
    return _counts(s, _active(stmt, device_id))


def month_counts(s: Session, year: int, device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = (select(_MONTH, func.count()).where(_YEAR == year)
            .group_by(_MONTH).order_by(_MONTH))
    return _counts(s, _active(stmt, device_id))


def day_counts(s: Session, year: int, month: int,
               device_id: str | None = None) -> list[tuple[int, int]]:
    stmt = (select(_DAY, func.count()).where(_YEAR == year, _MONTH == month)
            .group_by(_DAY).order_by(_DAY))
    return _counts(s, _active(stmt, device_id))


def list_day(s: Session, day: date, device_id: str | None = None) -> list[Capture]:
    stmt = select(Capture).where(Capture.day == day).order_by(Capture.recorded_at)
    return list(s.scalars(_active(stmt, device_id)))


def count_estimated(s: Session) -> int:
    stmt = select(func.count()).select_from(Capture).where(Capture.date_estimated.is_(True))
    return s.scalar(_active(stmt, None))


def list_estimated(s: Session) -> list[Capture]:
    stmt = (select(Capture).where(Capture.date_estimated.is_(True))
            .order_by(Capture.recorded_at.desc()))
    return list(s.scalars(_active(stmt, None)))


def list_trashed(s: Session) -> list[Capture]:
    stmt = (select(Capture).where(Capture.trashed_at.is_not(None))
            .order_by(Capture.trashed_at.desc()))
    return list(s.scalars(stmt))
```

- [ ] **Step 4: Contesto e route**

`backend/secondbrain/web/context.py`:

```python
"""Contesto comune delle pagine del finder: filtro dispositivo, fuso, albero."""
from sqlalchemy.orm import Session
from starlette.requests import Request

from .. import catalog
from ..models import WebSession
from ..naming import is_valid_device_id

LOCAL_FORMAT = "%d/%m/%Y %H:%M:%S"


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


def page_context(request: Request, db: Session, session: WebSession, device: str | None = None,
                 year: int | None = None, month: int | None = None) -> dict:
    return base_context(request, db, session, device) | {
        "year": year,
        "month": month,
        "tree": {
            "years": catalog.year_counts(db, device),
            "months": catalog.month_counts(db, year, device) if year else [],
            "days": catalog.day_counts(db, year, month, device) if year and month else [],
        },
    }
```

`backend/secondbrain/web/browse.py`:

```python
"""Navigazione del finder: anni, mesi, giorni, dettaglio, audio e file (spec §7)."""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import catalog
from ..models import Capture, WebSession
from .context import base_context, clean_device, page_context
from .deps import get_db, require_login
from .templating import MONTHS, templates

router = APIRouter()


def _capture(db: Session, capture_id: uuid.UUID) -> Capture:
    capture = catalog.get_capture(db, capture_id)
    if capture is None:
        raise HTTPException(404, "registrazione sconosciuta")
    return capture


@router.get("/browse")
def browse_root(request: Request, device: str | None = None, db: Session = Depends(get_db),
                session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session, clean_device(device))
    items = [(str(y), n, f"/browse/{y}{ctx['qs']}") for y, n in ctx["tree"]["years"]]
    return templates.TemplateResponse(request, "grid.html",
                                      ctx | {"crumbs": [("Archivio", None)], "items": items})


@router.get("/browse/{year}")
def browse_year(request: Request, year: int = Path(ge=1970, le=9999), device: str | None = None,
                db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session, clean_device(device), year)
    qs = ctx["qs"]
    items = [(MONTHS[m - 1], n, f"/browse/{year}/{m:02d}{qs}") for m, n in ctx["tree"]["months"]]
    crumbs = [("Archivio", f"/browse{qs}"), (str(year), None)]
    return templates.TemplateResponse(request, "grid.html", ctx | {"crumbs": crumbs, "items": items})


@router.get("/browse/{year}/{month}")
def browse_month(request: Request, year: int = Path(ge=1970, le=9999),
                 month: int = Path(ge=1, le=12), device: str | None = None,
                 db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session, clean_device(device), year, month)
    qs = ctx["qs"]
    items = [(str(d), n, f"/browse/{year}/{month:02d}/{d:02d}{qs}") for d, n in ctx["tree"]["days"]]
    crumbs = [("Archivio", f"/browse{qs}"), (str(year), f"/browse/{year}{qs}"),
              (MONTHS[month - 1], None)]
    return templates.TemplateResponse(request, "grid.html", ctx | {"crumbs": crumbs, "items": items})


@router.get("/browse/{year}/{month}/{day}")
def browse_day(request: Request, year: int = Path(ge=1970, le=9999),
               month: int = Path(ge=1, le=12), day: int = Path(ge=1, le=31),
               device: str | None = None, db: Session = Depends(get_db),
               session: WebSession = Depends(require_login)):
    try:
        the_day = date(year, month, day)
    except ValueError:
        raise HTTPException(404, "giorno inesistente") from None
    device = clean_device(device)
    ctx = page_context(request, db, session, device, year, month)
    qs = ctx["qs"]
    crumbs = [("Archivio", f"/browse{qs}"), (str(year), f"/browse/{year}{qs}"),
              (MONTHS[month - 1], f"/browse/{year}/{month:02d}{qs}"), (str(day), None)]
    return templates.TemplateResponse(request, "day.html", ctx | {
        "crumbs": crumbs, "day_num": day, "captures": catalog.list_day(db, the_day, device)})


@router.get("/captures/{capture_id}")
def capture_detail(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                   session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    files = request.app.state.archive.related_files(capture.rel_path)
    return templates.TemplateResponse(request, "_detail.html",
                                      base_context(request, db, session) | {"c": capture, "files": files})


@router.get("/captures/{capture_id}/audio")
def capture_audio(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    return FileResponse(request.app.state.archive.abs(capture.rel_path), media_type="audio/wav")


@router.get("/captures/{capture_id}/files/{name}")
def capture_file(request: Request, capture_id: uuid.UUID, name: str,
                 db: Session = Depends(get_db), session: WebSession = Depends(require_login)):
    capture = _capture(db, capture_id)
    archive = request.app.state.archive
    if name not in archive.related_files(capture.rel_path):
        raise HTTPException(404, "file sconosciuto")
    return FileResponse(archive.abs(capture.rel_path).parent / name, filename=name)
```

In `backend/secondbrain/app.py`: `from .web import browse as web_browse` e `app.include_router(web_browse.router)` dopo il router di login.

- [ ] **Step 5: Template**

`backend/secondbrain/web/templates/finder.html`:

```html
{% extends "base.html" %}
{% block body %}
<header class="top">
  <a class="brand" href="/browse{{ qs }}">Second Brain</a>
  <form method="get">
    <select name="device" aria-label="Dispositivo" onchange="this.form.submit()">
      <option value="">Tutti i dispositivi</option>
      {% for d in devices %}<option value="{{ d.id }}"{% if d.id == device %} selected{% endif %}>{{ d.name }}</option>{% endfor %}
    </select>
  </form>
  <nav>
    <a href="/fix">Da sistemare{% if estimated_count %} ({{ estimated_count }}){% endif %}</a>
    <a href="/trash">Cestino</a>
    <a href="/devices">Dispositivi</a>
    <form method="post" action="/logout"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit">Esci</button></form>
  </nav>
</header>
<div class="layout">
  <aside class="tree">
    <input type="checkbox" id="tree-toggle" hidden>
    <label for="tree-toggle" class="tree-toggle">Archivio ▾</label>
    <ul>
    {% for y, n in tree.years %}
      <li><a href="/browse/{{ y }}{{ qs }}"{% if y == year %} class="current"{% endif %}>{{ y }}</a> <span class="count">{{ n }}</span>
      {% if y == year and tree.months %}
        <ul>
        {% for m, mn in tree.months %}
          <li><a href="/browse/{{ y }}/{{ '%02d' % m }}{{ qs }}"{% if m == month %} class="current"{% endif %}>{{ MONTHS[m - 1] }}</a> <span class="count">{{ mn }}</span>
          {% if m == month and tree.days %}
            <ul>
            {% for d, dn in tree.days %}
              <li><a href="/browse/{{ y }}/{{ '%02d' % m }}/{{ '%02d' % d }}{{ qs }}"{% if d == day_num %} class="current"{% endif %}>{{ d }}</a> <span class="count">{{ dn }}</span></li>
            {% endfor %}
            </ul>
          {% endif %}
          </li>
        {% endfor %}
        </ul>
      {% endif %}
      </li>
    {% else %}
      <li class="empty">Nessuna registrazione</li>
    {% endfor %}
    </ul>
  </aside>
  <main>
    {% if crumbs %}<nav class="crumbs">{% for label, href in crumbs %}{% if not loop.first %} › {% endif %}{% if href %}<a href="{{ href }}">{{ label }}</a>{% else %}{{ label }}{% endif %}{% endfor %}</nav>{% endif %}
    {% block main %}{% endblock %}
  </main>
</div>
{% endblock %}
```

`backend/secondbrain/web/templates/grid.html`:

```html
{% extends "finder.html" %}
{% block main %}
{% if items %}
<div class="grid">
  {% for label, count, href in items %}
  <a href="{{ href }}"><span class="label">{{ label }}</span><span class="count">{{ count }}</span></a>
  {% endfor %}
</div>
{% else %}<p class="empty">Nessuna registrazione.</p>{% endif %}
{% endblock %}
```

`backend/secondbrain/web/templates/day.html`:

```html
{% extends "finder.html" %}
{% block main %}
{% for c in captures %}{% include "_row.html" %}{% else %}<p class="empty">Nessuna registrazione in questo giorno.</p>{% endfor %}
{% endblock %}
```

`backend/secondbrain/web/templates/_row.html`:

```html
<div class="row" id="row-{{ c.id }}">
  <button type="button" class="row-main" hx-get="/captures/{{ c.id }}" hx-target="#detail-{{ c.id }}">
    <span class="time">{% if show_day %}{{ c.recorded_at.astimezone(tz).strftime('%d/%m/%Y') }} {% endif %}{{ c.recorded_at.astimezone(tz).strftime('%H:%M:%S') }}</span>
    <span class="title">{{ c.title or 'senza titolo' }}</span>
    {% if c.date_estimated %}<span class="badge">data stimata</span>{% endif %}
    <span class="meta">{{ device_names.get(c.device_id, c.device_id) }} · {{ c.duration_s|duration }} · {{ c.size_bytes|size }}</span>
  </button>
  <div class="detail" id="detail-{{ c.id }}"></div>
</div>
```

`backend/secondbrain/web/templates/_detail.html`:

```html
<audio controls preload="none" src="/captures/{{ c.id }}/audio"></audio>
<dl>
  <dt>Registrata</dt><dd>{{ local(c.recorded_at) }}{% if c.date_estimated %} <span class="badge">stimata: ora di ricezione</span>{% endif %}</dd>
  <dt>Ricevuta</dt><dd>{{ local(c.received_at) }}</dd>
  <dt>Dispositivo</dt><dd>{{ device_names.get(c.device_id, c.device_id) }} <span class="mono">{{ c.device_id }}</span></dd>
  <dt>Firmware</dt><dd>{{ c.firmware_version or '—' }}</dd>
  <dt>Alimentazione</dt><dd>{% if c.power_source == 'usb' %}USB{% elif c.power_source == 'battery' %}batteria{% if c.battery_pct is not none %} {{ c.battery_pct }} %{% endif %}{% else %}—{% endif %}{% if c.battery_v %} · {{ '%.2f'|format(c.battery_v) }} V{% endif %}</dd>
  <dt>ID cattura</dt><dd class="mono">{{ c.capture_id }}</dd>
  <dt>SHA-256</dt><dd class="mono">{{ c.sha256 }}</dd>
  <dt>File</dt><dd><ul class="files">{% for name in files %}<li><a href="/captures/{{ c.id }}/files/{{ name }}">{{ name }}</a></li>{% endfor %}</ul></dd>
</dl>
<button type="button" onclick="this.closest('.detail').innerHTML=''">Chiudi</button>
```

- [ ] **Step 6: Test verdi e controllo a vista**

Run: `uv run pytest -q`
Expected: tutti PASS (se `test_audio_supports_range` riceve 200 invece di 206, la Starlette installata è troppo vecchia: `uv lock --upgrade-package starlette`).

Il controllo a vista con il device e un browser vero si fa nel Task 13.

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "backend: finder web con albero anno/mese/giorno, dettaglio e ascolto

Pagine renderizzate dal server con htmx per espandere una registrazione senza
ricaricare. Il dettaglio elenca tutti i file con lo stesso nome base, quindi i file
dell'AI compariranno senza toccare la UI. L'audio si serve con Range per potersi
spostare nel file dal browser; la route dei file serve solo i correlati della
registrazione, mai un percorso arbitrario."
```

---

### Task 12: Azioni — titolo, correggi data, cestino, da sistemare, dispositivi

**Files:**
- Create: `backend/secondbrain/library.py`, `backend/secondbrain/web/actions.py`
- Create: `backend/secondbrain/web/templates/trash.html`, `fix.html`, `devices.html`
- Modify: `backend/secondbrain/web/templates/_detail.html` (azioni), `backend/secondbrain/app.py` (router, pulizia del cestino nel lifespan), `backend/secondbrain/cli.py` (`trash purge`)
- Test: `backend/tests/test_library.py`, `backend/tests/test_actions.py`

**Interfaces:**
- Consumes: `Archive.move`, `Archive.delete`, `Archive.write_sidecar`, `archive.TRASH`, `naming.day_dir`, `naming.local_day`, `sidecar.capture_to_sidecar`, `devices.rename_device`, `ota.current_release`, `web.context.*`, `web.deps.*`.
- Produces: `library.NotFound(LookupError)`, `InTrash(ValueError)`, `NotInTrash(ValueError)`, `set_title(s, archive, capture_id, title) -> Capture`, `correct_recorded_at(s, archive, tz, capture_id, local: datetime) -> Capture` (`local` senza fuso, ora locale), `trash_capture(s, archive, capture_id, now) -> Capture`, `restore_capture(s, archive, capture_id) -> Capture`, `delete_capture(s, archive, capture_id) -> None`, `purge_trash(s, archive, now, retention_days) -> int`; tutte fanno commit. Route: `POST /captures/{id}/title`, `POST /captures/{id}/recorded-at`, `POST /captures/{id}/trash`, `GET /trash`, `POST /trash/{id}/restore`, `POST /trash/{id}/delete`, `GET /fix`, `GET /devices`, `POST /devices/{id}/name`. Comando `secondbrain trash purge`.

- [ ] **Step 1: Test di `library` (falliscono)**

`backend/tests/test_library.py`:

```python
import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from secondbrain import library
from secondbrain.archive import Archive
from secondbrain.models import Capture
from tests.helpers import NOW, capture_by, make_wav, upload

ROME = ZoneInfo("Europe/Rome")
CID = "cap_20260923_191530"
BASE = "211530_70041dd8263c"


@pytest.fixture
def arch(settings):
    return Archive(settings.archive_dir)


def test_set_title_updates_db_and_sidecar(recordings, db, arch):
    cap = library.set_title(db, arch, capture_by(db, CID).id, "  Idea per il digest  ")
    assert cap.title == "Idea per il digest"
    assert arch.read_sidecar(cap.rel_path)["title"] == "Idea per il digest"
    assert library.set_title(db, arch, cap.id, "   ").title is None


def test_correct_date_moves_derived_files(recordings, db, arch, settings):
    cap = capture_by(db, CID)
    arch.abs(cap.rel_path).with_suffix(".transcript.md").write_text("ciao")
    cap = library.correct_recorded_at(db, arch, ROME, cap.id, datetime(2026, 9, 20, 18, 5))
    assert cap.rel_path == f"2026/09/20/{BASE}.wav" and cap.day == date(2026, 9, 20)
    assert cap.recorded_at == datetime(2026, 9, 20, 16, 5, tzinfo=UTC)
    assert not cap.date_estimated
    assert arch.related_files(cap.rel_path) == [f"{BASE}.json", f"{BASE}.transcript.md", f"{BASE}.wav"]
    sidecar = arch.read_sidecar(cap.rel_path)
    assert sidecar["recorded_at"] == "2026-09-20T16:05:00+00:00" and sidecar["date_estimated"] is False
    assert not (settings.archive_dir / f"2026/09/23/{BASE}.wav").exists()


def test_correct_time_on_the_same_day_does_not_move(recordings, db, arch):
    cap = library.correct_recorded_at(db, arch, ROME, capture_by(db, CID).id, datetime(2026, 9, 23, 7, 0))
    assert cap.rel_path == f"2026/09/23/{BASE}.wav"


def test_correct_date_into_an_occupied_name(recordings, db, arch, settings):
    target = settings.archive_dir / "2026/09/20"
    target.mkdir(parents=True)
    (target / f"{BASE}.wav").write_bytes(b"altro")
    cap = library.correct_recorded_at(db, arch, ROME, capture_by(db, CID).id, datetime(2026, 9, 20, 12, 0))
    assert cap.rel_path == f"2026/09/20/{BASE}_2.wav"


def test_trash_restore_delete(recordings, db, arch, settings):
    cap = library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    assert cap.rel_path == f".trash/2026/09/23/{BASE}.wav" and cap.trashed_at == NOW
    assert arch.read_sidecar(cap.rel_path)["trashed_at"] == NOW.isoformat()
    cap = library.restore_capture(db, arch, cap.id)
    assert cap.rel_path == f"2026/09/23/{BASE}.wav" and cap.trashed_at is None
    with pytest.raises(library.NotInTrash):
        library.delete_capture(db, arch, cap.id)
    library.trash_capture(db, arch, cap.id, NOW)
    library.delete_capture(db, arch, cap.id)
    assert db.get(Capture, cap.id) is None
    assert not (settings.archive_dir / ".trash/2026").exists()


def test_restore_into_an_occupied_name(recordings, db, arch, settings):
    cap = library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    (settings.archive_dir / f"2026/09/23/{BASE}.wav").write_bytes(b"altro")
    assert library.restore_capture(db, arch, cap.id).rel_path == f"2026/09/23/{BASE}_2.wav"


def test_correct_date_refused_in_trash(recordings, db, arch):
    cap = library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    with pytest.raises(library.InTrash):
        library.correct_recorded_at(db, arch, ROME, cap.id, datetime(2026, 9, 20, 12, 0))


def test_purge_respects_retention(recordings, db, arch):
    old = library.trash_capture(db, arch, capture_by(db, CID).id, NOW - timedelta(days=31))
    recent = library.trash_capture(db, arch, capture_by(db, "cap_20260923_080000").id,
                                   NOW - timedelta(days=29))
    assert library.purge_trash(db, arch, NOW, 30) == 1
    assert db.get(Capture, old.id) is None and db.get(Capture, recent.id) is not None


def test_unknown_capture(db, arch):
    with pytest.raises(library.NotFound):
        library.set_title(db, arch, uuid.uuid4(), "x")


def test_device_retry_after_trash_is_still_a_duplicate(recordings, db, arch):
    library.trash_capture(db, arch, capture_by(db, CID).id, NOW)
    assert upload(recordings.client, make_wav(fill=b"\x01\x00")).status_code == 409
```

Run: `uv run pytest tests/test_library.py -q`
Expected: FAIL, `ImportError` su `secondbrain.library`.

- [ ] **Step 2: Implementazione di `library`**

`backend/secondbrain/library.py`:

```python
"""Operazioni sulle registrazioni che toccano disco e catalogo (spec §4, §7).

Ordine sempre uguale: prima il disco (file e sidecar), poi il commit. Se il commit
fallisce il disco ha già ragione e `rescan` riallinea il catalogo.
"""
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from .archive import TRASH, Archive
from .models import Capture
from .naming import day_dir, local_day
from .sidecar import capture_to_sidecar

MAX_TITLE_LEN = 200


class NotFound(LookupError):
    """Registrazione sconosciuta."""


class InTrash(ValueError):
    """Operazione non permessa su una registrazione nel cestino."""


class NotInTrash(ValueError):
    """Operazione permessa solo dal cestino."""


def _get(s: Session, capture_id: uuid.UUID) -> Capture:
    capture = s.get(Capture, capture_id)
    if capture is None:
        raise NotFound(str(capture_id))
    return capture


def _save(s: Session, archive: Archive, capture: Capture) -> None:
    archive.write_sidecar(capture.rel_path, capture_to_sidecar(capture))
    s.commit()


def set_title(s: Session, archive: Archive, capture_id: uuid.UUID, title: str) -> Capture:
    capture = _get(s, capture_id)
    capture.title = title.strip()[:MAX_TITLE_LEN] or None
    _save(s, archive, capture)
    return capture


def correct_recorded_at(s: Session, archive: Archive, tz: ZoneInfo, capture_id: uuid.UUID,
                        local: datetime) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        raise InTrash("la registrazione è nel cestino")
    new_utc = local.replace(tzinfo=tz).astimezone(UTC)
    new_day = local_day(new_utc, tz)
    if new_day != capture.day:
        capture.rel_path = archive.move(capture.rel_path, day_dir(new_day))
        capture.day = new_day
    capture.recorded_at = new_utc
    capture.date_estimated = False
    _save(s, archive, capture)
    return capture


def trash_capture(s: Session, archive: Archive, capture_id: uuid.UUID, now: datetime) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is None:
        capture.rel_path = archive.move(capture.rel_path, f"{TRASH}/{day_dir(capture.day)}")
        capture.trashed_at = now
        _save(s, archive, capture)
    return capture


def restore_capture(s: Session, archive: Archive, capture_id: uuid.UUID) -> Capture:
    capture = _get(s, capture_id)
    if capture.trashed_at is not None:
        capture.rel_path = archive.move(capture.rel_path, day_dir(capture.day))
        capture.trashed_at = None
        _save(s, archive, capture)
    return capture


def delete_capture(s: Session, archive: Archive, capture_id: uuid.UUID) -> None:
    capture = _get(s, capture_id)
    if capture.trashed_at is None:
        raise NotInTrash("si elimina definitivamente solo dal cestino")
    archive.delete(capture.rel_path)
    s.delete(capture)
    s.commit()


def purge_trash(s: Session, archive: Archive, now: datetime, retention_days: int) -> int:
    limit = now - timedelta(days=retention_days)
    expired = s.scalars(select(Capture).where(Capture.trashed_at.is_not(None),
                                              Capture.trashed_at < limit)).all()
    for capture in expired:
        archive.delete(capture.rel_path)
        s.delete(capture)
    s.commit()
    return len(expired)
```

Run: `uv run pytest tests/test_library.py -q`
Expected: PASS.

- [ ] **Step 3: Test delle route (falliscono)**

`backend/tests/test_actions.py`:

```python
from datetime import timedelta

from secondbrain import library
from secondbrain.archive import Archive
from secondbrain.cli import main
from secondbrain.clock import utcnow
from secondbrain.models import Capture
from secondbrain.ota import publish
from tests.helpers import DEV, NOW, TEST_DB, capture_by, make_wav, upload

CID = "cap_20260923_191530"


def htmx(ui):
    return {"X-CSRF-Token": ui.csrf, "HX-Request": "true"}


def test_actions_require_csrf(recordings, db):
    cap = capture_by(db, CID)
    assert recordings.client.post(f"/captures/{cap.id}/title", data={"title": "x"}).status_code == 403
    assert recordings.client.post(f"/captures/{cap.id}/trash").status_code == 403


def test_title_returns_the_updated_row(recordings, db):
    cap = capture_by(db, CID)
    r = recordings.client.post(f"/captures/{cap.id}/title", data={"title": "Idea"}, headers=htmx(recordings))
    assert r.status_code == 200
    assert f'id="row-{cap.id}"' in r.text and "Idea" in r.text


def test_detail_shows_actions(recordings, db):
    cap = capture_by(db, CID)
    text = recordings.client.get(f"/captures/{cap.id}").text
    assert f'hx-post="/captures/{cap.id}/recorded-at"' in text
    assert 'value="2026-09-23T21:15:30"' in text


def test_correct_date_redirects_to_the_new_day(recordings, db):
    cap = capture_by(db, CID)
    url = f"/captures/{cap.id}/recorded-at"
    r = recordings.client.post(url, data={"recorded_at": "2026-09-20T18:05"}, headers=htmx(recordings))
    assert r.status_code == 200 and r.headers["hx-redirect"] == "/browse/2026/09/20"
    for bad in ["ieri", "", "1999-01-01T10:00", "2026-09-20T18:05+02:00"]:
        r = recordings.client.post(url, data={"recorded_at": bad}, headers=htmx(recordings))
        assert r.status_code == 400, bad


def test_trash_and_restore_routes(recordings, db):
    cap = capture_by(db, CID)
    c = recordings.client
    r = c.post(f"/captures/{cap.id}/trash", headers=htmx(recordings))
    assert r.status_code == 200 and r.text == ""
    assert "21:15:30" in c.get("/trash").text
    assert "21:15:30" not in c.get("/browse/2026/09/23").text
    r = c.post(f"/trash/{cap.id}/restore", data={"csrf": recordings.csrf}, follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/trash")
    assert "21:15:30" in c.get("/browse/2026/09/23").text


def test_delete_only_from_trash(recordings, db):
    cap = capture_by(db, CID)
    c = recordings.client
    form = {"csrf": recordings.csrf}
    assert c.post(f"/trash/{cap.id}/delete", data=form, follow_redirects=False).status_code == 409
    c.post(f"/captures/{cap.id}/trash", headers=htmx(recordings))
    assert c.post(f"/trash/{cap.id}/delete", data=form, follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(Capture, cap.id) is None


def test_fix_lists_estimated_recordings(recordings):
    upload(recordings.client, make_wav(fill=b"\x09\x00"), capture_id="cap_unsynced_000001", ts=None)
    text = recordings.client.get("/fix").text
    assert "28/09/2026 14:00:00" in text and "Da sistemare (1)" in text


def test_devices_page_and_rename(recordings, db, settings, tmp_path):
    fw = tmp_path / "fw.bin"
    fw.write_bytes(b"\xe9fw")
    publish(db, settings.firmware_dir, fw, "epaper154", "0.7.0", NOW)
    db.commit()
    c = recordings.client
    text = c.get("/devices").text
    assert DEV in text and "pubblicata 0.7.0" in text
    r = c.post(f"/devices/{DEV}/name", data={"name": "scrivania", "csrf": recordings.csrf},
               follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/devices")
    assert "scrivania" in c.get("/devices").text
    r = c.post(f"/devices/{DEV}/name", data={"name": "  ", "csrf": recordings.csrf})
    assert r.status_code == 400


def test_old_trash_is_purged_at_startup(recordings, db, settings, make_client):
    cap = capture_by(db, CID)
    library.trash_capture(db, Archive(settings.archive_dir), cap.id, NOW - timedelta(days=40))
    make_client(settings)
    db.expire_all()
    assert db.get(Capture, cap.id) is None


def test_cli_trash_purge(recordings, db, settings, monkeypatch, capsys):
    cap = capture_by(db, CID)
    # la CLI usa l'orologio vero, non FakeClock
    library.trash_capture(db, Archive(settings.archive_dir), cap.id, utcnow() - timedelta(days=40))
    monkeypatch.setenv("DATABASE_URL", TEST_DB)
    monkeypatch.setenv("ARCHIVE_DIR", str(settings.archive_dir))
    assert main(["trash", "purge"]) == 0
    assert "Eliminate definitivamente 1" in capsys.readouterr().out
```

Run: `uv run pytest tests/test_actions.py -q`
Expected: FAIL (route inesistenti).

- [ ] **Step 4: Route e template**

`backend/secondbrain/web/actions.py`:

```python
"""Azioni sulle registrazioni e pagine cestino, da sistemare, dispositivi (spec §7)."""
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.orm import Session

from .. import catalog, library, ota
from ..devices import DeviceError, rename_device
from ..models import WebSession
from ..naming import day_dir
from .context import base_context, page_context
from .deps import get_db, require_csrf, require_login
from .templating import templates

MIN_YEAR = 2000
router = APIRouter()


def _archive(request: Request):
    return request.app.state.archive


@router.post("/captures/{capture_id}/title")
def set_title_route(request: Request, capture_id: uuid.UUID, title: str = Form(""),
                    db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        capture = library.set_title(db, _archive(request), capture_id, title)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return templates.TemplateResponse(request, "_row.html",
                                      base_context(request, db, session) | {"c": capture})


@router.post("/captures/{capture_id}/recorded-at")
def correct_date_route(request: Request, capture_id: uuid.UUID, recorded_at: str = Form(""),
                       db: Session = Depends(get_db), session: WebSession = Depends(require_csrf)):
    try:
        local = datetime.fromisoformat(recorded_at)
    except ValueError:
        return PlainTextResponse("data/ora non valida", status_code=400)
    if local.tzinfo is not None or local.year < MIN_YEAR:
        return PlainTextResponse("data/ora non valida", status_code=400)
    try:
        capture = library.correct_recorded_at(db, _archive(request),
                                              request.app.state.settings.tz_archive,
                                              capture_id, local)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.InTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return HTMLResponse("", headers={"HX-Redirect": f"/browse/{day_dir(capture.day)}"})


@router.post("/captures/{capture_id}/trash")
def trash_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                session: WebSession = Depends(require_csrf)):
    try:
        library.trash_capture(db, _archive(request), capture_id, request.app.state.clock())
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return HTMLResponse("")  # htmx sostituisce la riga con niente


@router.get("/trash")
def trash_page(request: Request, db: Session = Depends(get_db),
               session: WebSession = Depends(require_login)):
    return templates.TemplateResponse(request, "trash.html", page_context(request, db, session) | {
        "crumbs": [("Cestino", None)], "captures": catalog.list_trashed(db),
        "retention_days": request.app.state.settings.trash_retention_days})


@router.post("/trash/{capture_id}/restore")
def restore_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                  session: WebSession = Depends(require_csrf)):
    try:
        library.restore_capture(db, _archive(request), capture_id)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    return RedirectResponse("/trash", status_code=303)


@router.post("/trash/{capture_id}/delete")
def delete_route(request: Request, capture_id: uuid.UUID, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    try:
        library.delete_capture(db, _archive(request), capture_id)
    except library.NotFound:
        raise HTTPException(404, "registrazione sconosciuta") from None
    except library.NotInTrash as exc:
        return PlainTextResponse(str(exc), status_code=409)
    return RedirectResponse("/trash", status_code=303)


@router.get("/fix")
def fix_page(request: Request, db: Session = Depends(get_db),
             session: WebSession = Depends(require_login)):
    return templates.TemplateResponse(request, "fix.html", page_context(request, db, session) | {
        "crumbs": [("Da sistemare", None)], "captures": catalog.list_estimated(db),
        "show_day": True})


@router.get("/devices")
def devices_page(request: Request, db: Session = Depends(get_db),
                 session: WebSession = Depends(require_login)):
    ctx = page_context(request, db, session)
    releases = {d.type: ota.current_release(db, d.type) for d in ctx["devices"]}
    return templates.TemplateResponse(request, "devices.html", ctx | {
        "crumbs": [("Dispositivi", None)], "releases": releases})


@router.post("/devices/{device_id}/name")
def rename_route(device_id: str, name: str = Form(""), db: Session = Depends(get_db),
                 session: WebSession = Depends(require_csrf)):
    try:
        rename_device(db, device_id, name)
    except DeviceError as exc:
        return PlainTextResponse(str(exc), status_code=400)
    db.commit()
    return RedirectResponse("/devices", status_code=303)
```

In `backend/secondbrain/web/templates/_detail.html`, prima del pulsante "Chiudi":

```html
{% if not c.trashed_at %}
<div class="actions">
  <form hx-post="/captures/{{ c.id }}/title" hx-target="#row-{{ c.id }}" hx-swap="outerHTML">
    <input name="title" value="{{ c.title or '' }}" placeholder="Titolo" maxlength="200" aria-label="Titolo">
    <button type="submit">Salva titolo</button>
  </form>
  <form hx-post="/captures/{{ c.id }}/recorded-at">
    <input type="datetime-local" name="recorded_at" step="1" required aria-label="Data e ora" value="{{ c.recorded_at.astimezone(tz).strftime('%Y-%m-%dT%H:%M:%S') }}">
    <button type="submit">Correggi data/ora</button>
  </form>
  <button type="button" hx-post="/captures/{{ c.id }}/trash" hx-target="#row-{{ c.id }}" hx-swap="outerHTML" hx-confirm="Spostare la registrazione nel cestino?">Cestino</button>
</div>
{% endif %}
```

`backend/secondbrain/web/templates/trash.html`:

```html
{% extends "finder.html" %}
{% block main %}
<p class="empty">Le registrazioni nel cestino si eliminano da sole dopo {{ retention_days }} giorni.</p>
{% if captures %}
<div class="table-wrap"><table>
<tr><th>Registrata</th><th>Titolo</th><th>Dispositivo</th><th>Nel cestino dal</th><th></th></tr>
{% for c in captures %}
<tr>
  <td>{{ local(c.recorded_at) }}</td>
  <td>{{ c.title or 'senza titolo' }}</td>
  <td>{{ device_names.get(c.device_id, c.device_id) }}</td>
  <td>{{ local(c.trashed_at) }}</td>
  <td class="actions">
    <a href="/captures/{{ c.id }}/audio">Ascolta</a>
    <form method="post" action="/trash/{{ c.id }}/restore"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit">Ripristina</button></form>
    <form method="post" action="/trash/{{ c.id }}/delete" onsubmit="return confirm('Eliminare definitivamente? Non si può annullare.')"><input type="hidden" name="csrf" value="{{ csrf }}"><button type="submit">Elimina</button></form>
  </td>
</tr>
{% endfor %}
</table></div>
{% else %}<p class="empty">Il cestino è vuoto.</p>{% endif %}
{% endblock %}
```

`backend/secondbrain/web/templates/fix.html`:

```html
{% extends "finder.html" %}
{% block main %}
<p class="empty">Registrazioni arrivate senza un orario affidabile: la data è quella di ricezione. Apri una registrazione e correggi data e ora.</p>
{% for c in captures %}{% include "_row.html" %}{% else %}<p class="empty">Niente da sistemare.</p>{% endfor %}
{% endblock %}
```

`backend/secondbrain/web/templates/devices.html`:

```html
{% extends "finder.html" %}
{% block main %}
{% if devices %}
<div class="table-wrap"><table>
<tr><th>Nome</th><th>ID</th><th>Tipo</th><th>Ultimo contatto</th><th>Firmware</th><th>Alimentazione</th><th>Token</th></tr>
{% for d in devices %}
{% set rel = releases.get(d.type) %}
<tr>
  <td><form method="post" action="/devices/{{ d.id }}/name" class="actions"><input type="hidden" name="csrf" value="{{ csrf }}"><input name="name" value="{{ d.name }}" maxlength="100" aria-label="Nome"><button type="submit">Rinomina</button></form></td>
  <td class="mono">{{ d.id }}</td>
  <td>{{ d.type }}</td>
  <td>{{ local(d.last_seen_at) if d.last_seen_at else 'mai' }}</td>
  <td>{{ d.last_firmware or '—' }}{% if rel %} <span class="count">(pubblicata {{ rel.version }})</span>{% endif %}</td>
  <td>{% if d.last_power_source == 'usb' %}USB{% elif d.last_power_source == 'battery' %}batteria{% if d.last_battery_pct is not none %} {{ d.last_battery_pct }} %{% endif %}{% else %}—{% endif %}{% if d.last_battery_v %} · {{ '%.2f'|format(d.last_battery_v) }} V{% endif %}</td>
  <td>{{ 'sì' if d.token_hash else 'no' }}</td>
</tr>
{% endfor %}
</table></div>
{% else %}<p class="empty">Nessun dispositivo: si registrano con <code>secondbrain device add</code>.</p>{% endif %}
{% endblock %}
```

- [ ] **Step 5: Pulizia del cestino nel lifespan, router, CLI**

In `backend/secondbrain/app.py`:
- import: `import asyncio`, `from contextlib import asynccontextmanager, suppress`, `from fastapi.concurrency import run_in_threadpool`, `from . import ingest, library, ota`, `from .web import actions as web_actions`;
- costante in cima: `PURGE_INTERVAL_S = 24 * 3600`;
- funzioni a livello di modulo:

```python
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
```

- il lifespan diventa:

```python
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
        app.state.engine.dispose()
```

- `app.include_router(web_actions.router)` dopo il router del finder.

In `backend/secondbrain/cli.py` aggiungere `from . import library` e:

```python
def _trash_purge(args: argparse.Namespace) -> None:
    with open_session() as (s, settings):
        removed = library.purge_trash(s, Archive(settings.archive_dir), utcnow(),
                                      settings.trash_retention_days)
    print(f"Eliminate definitivamente {removed} registrazioni scadute nel cestino.")


def _add_trash_commands(sub) -> None:
    group = sub.add_parser("trash", help="cestino")
    actions = group.add_subparsers(dest="action", required=True)
    actions.add_parser("purge", help="elimina quelle nel cestino da oltre TRASH_RETENTION_DAYS giorni") \
        .set_defaults(func=_trash_purge)
```

e aggiungere `_add_trash_commands` in fondo a `COMMAND_GROUPS`.

- [ ] **Step 6: Test verdi**

Run: `uv run pytest -q`
Expected: tutti PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/
git commit -m "backend: titolo, correzione della data, cestino e pagine di servizio del finder

Correggere la data sposta la registrazione e tutti i suoi derivati nella cartella del
giorno giusto senza cambiarne il nome; le catture con data stimata si ritrovano in
'Da sistemare'. Il cestino conserva la struttura per giorno ed e' ripulito all'avvio
e ogni 24 ore dopo TRASH_RETENTION_DAYS. Una cattura cestinata che il device
ritenta resta un duplicato (409) e non ricompare."
```

---

### Task 13: Docker compose, README e verifica end-to-end sul Mac

**Files:**
- Create: `backend/Dockerfile`, `backend/docker-entrypoint.sh`, `backend/docker-compose.yml`, `backend/.env.example`, `backend/.dockerignore`, `backend/README.md`
- Modify: `README.md` (rimando a `backend/README.md`)

**Interfaces:**
- Consumes: `secondbrain.app:create_app_from_env`, `alembic.ini`, CLI completa.
- Produces: immagine `app` in ascolto su 8000 (health `/healthz`), servizio `postgres`, profilo `tunnel` con `cloudflared`; variabili documentate in `.env.example`.

- [ ] **Step 1: File di deploy**

`backend/Dockerfile`:

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini docker-entrypoint.sh ./
COPY migrations ./migrations
COPY secondbrain ./secondbrain
RUN uv sync --frozen --no-dev && chmod +x docker-entrypoint.sh
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
ENTRYPOINT ["/app/docker-entrypoint.sh"]
```

`backend/docker-entrypoint.sh`:

```sh
#!/bin/sh
# Migrazioni a ogni avvio (idempotenti), poi il servizio.
set -e
alembic -c /app/alembic.ini upgrade head
exec uvicorn --factory secondbrain.app:create_app_from_env --host 0.0.0.0 --port 8000
```

`backend/docker-compose.yml`:

```yaml
# Avvio: cp .env.example .env (e completarlo), poi docker compose up -d --build
# Con il tunnel Cloudflare: docker compose --profile tunnel up -d --build
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
    environment:
      DATABASE_URL: postgresql+psycopg://secondbrain:${POSTGRES_PASSWORD}@postgres:5432/secondbrain
      ARCHIVE_DIR: /data/archive
      FIRMWARE_DIR: /data/firmware
      TZ_ARCHIVE: ${TZ_ARCHIVE:-Europe/Rome}
      TRASH_RETENTION_DAYS: ${TRASH_RETENTION_DAYS:-30}
      ALLOW_UNAUTHENTICATED_LAN: ${ALLOW_UNAUTHENTICATED_LAN:-false}
      DEVICE_HOSTNAME: ${DEVICE_HOSTNAME:-}
    volumes:
      - ${DATA_DIR:-./data}/archive:/data/archive
      - ${DATA_DIR:-./data}/firmware:/data/firmware
    ports:
      - "${APP_PORT:-8000}:8000"

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

`backend/.env.example`:

```sh
# Copiare in .env e completare. .env non va in git.
POSTGRES_PASSWORD=cambia-questa-password
# Dove stanno archivio, firmware e dati di Postgres sull'host
DATA_DIR=./data
APP_PORT=8000
TZ_ARCHIVE=Europe/Rome
TRASH_RETENTION_DAYS=30
# Accetta upload e OTA senza token solo dalla LAN (IP privato, non dal tunnel): serve al
# firmware attuale, che non manda token. Dal tunnel il token resta sempre obbligatorio.
ALLOW_UNAUTHENTICATED_LAN=false
# Hostname pubblico dei dispositivi nel tunnel (es. ingest.esempio.it); vuoto se non c'è tunnel
DEVICE_HOSTNAME=
# Token del tunnel Cloudflare, serve solo con --profile tunnel
TUNNEL_TOKEN=
```

`backend/.dockerignore`:

```
.venv
__pycache__
.pytest_cache
tests
data
.env
```

- [ ] **Step 2: README del backend**

`backend/README.md` con queste sezioni, scritte per chi installa da zero (comandi completi, nessun rimando al piano):

1. **Cos'è** — due righe: riceve le registrazioni dai dispositivi, le archivia in `archive/AAAA/MM/GG/`, UI finder, OTA. Link alla spec.
2. **Installazione** — `cp .env.example .env`, generare la password (`openssl rand -base64 24`), `docker compose up -d --build`, `docker compose exec app secondbrain set-password`, `docker compose exec app secondbrain device add <mac> --name <nome>` (salvare il token), aprire `http://<host>:8000`. L'immagine si costruisce sulla macchina di destinazione (`--build`): la base `python:3.12-slim` è multi-architettura, quindi lo stesso compose va su amd64 (ZimaBoard, NAS UGREEN) e arm64 senza registry.
3. **Struttura dei dati** — albero di `DATA_DIR` (`archive/` con `.incoming/` e `.trash/`, `firmware/<tipo>/`, `postgres/`), nome base `HHMMSS_<device>[_k]`, sidecar `.json`, file derivati con lo stesso nome base.
4. **Comandi** — tabella: `device add|token|list`, `set-password`, `rescan`, `firmware publish|rollback|list`, `trash purge`, tutti come `docker compose exec app secondbrain …`. Per pubblicare un firmware: `docker compose cp ../firmware/build/secondbrain_fw.bin app:/tmp/fw.bin` e `docker compose exec app secondbrain firmware publish /tmp/fw.bin --version X.Y.Z`.
5. **Contratto dei dispositivi** — `POST /captures` (header, codici 201/409/4xx/5xx come in spec §6), `GET /firmware/manifest.json`, `Authorization: Bearer`; `ALLOW_UNAUTHENTICATED_LAN=true` accetta dispositivi senza token solo dalla LAN (IP privato e nessun `Cf-Connecting-Ip`), dal tunnel mai.
6. **Accesso da fuori con Cloudflare Tunnel** — creare il tunnel in Zero Trust → Networks → Tunnels (tipo cloudflared), copiare il token in `TUNNEL_TOKEN`; due public hostname verso `http://app:8000` (uno per la UI, uno per i dispositivi, quest'ultimo in `DEVICE_HOSTNAME`); `docker compose --profile tunnel up -d`; Bot Fight Mode può bloccare i dispositivi (vedi Task 14).
7. **Backup e ripristino** — archivio: snapshot del NAS o `rsync -a DATA_DIR/archive/ <destinazione>`; database: `docker compose exec -T postgres pg_dump -U secondbrain secondbrain > secondbrain.sql` (serve per dispositivi, token e release; le registrazioni si ricostruiscono dal disco). Ripristino su una macchina nuova: copiare `archive/` e `firmware/`, `docker compose up -d`, `psql` del dump oppure, senza dump, `secondbrain rescan` e registrare di nuovo dispositivi e release.
8. **Sviluppo** — `uv sync`, `docker compose -f docker-compose.test.yml up -d`, `uv run pytest -q`.

In `README.md` alla radice, nella sezione che descrive il repository, aggiungere una riga: "`backend/`: servizio `secondbrain` (archivio delle registrazioni, UI, OTA): vedi `backend/README.md`."

- [ ] **Step 3: Avvio sul Mac**

Run:
```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN   # se c'è capture_server.py, fermarlo (Ctrl-C nel suo terminale)
cd backend && cp .env.example .env
# in .env: POSTGRES_PASSWORD=<generata>, ALLOW_UNAUTHENTICATED_LAN=true
docker compose up -d --build
docker compose ps
curl -s http://localhost:8000/healthz
docker compose exec app secondbrain set-password
```
Expected: `app` e `postgres` in stato `healthy`, `{"status":"ok"}`, password impostata. Login da `http://localhost:8000` riuscito, archivio vuoto.

- [ ] **Step 4: Verifica end-to-end con il device vero**

Il device punta già a `http://192.168.1.28:8000` (base URL della rete di casa in `secrets.h`) e a `http://192.168.1.28:8000/firmware/manifest.json`: gli stessi percorsi di `capture_server.py`, quindi non serve riflashare. Se l'IP del Mac è cambiato, aggiornare `secrets.h` e flashare.

Verificare, annotando l'esito di ciascun punto:
1. Due registrazioni con PWR (una breve, una di ~30 s). `docker compose logs app | grep archiviata` mostra due righe; in `data/archive/<anno>/<mese>/<giorno>/` ci sono WAV e sidecar; il display del device mostra coda 0; nella UI compaiono con durata giusta; l'audio si ascolta dal browser anche spostandosi nel file.
2. Il dispositivo compare in "Dispositivi" (id `70041dd8263c`, firmware 0.6.2, "USB"); rinominarlo in "e-paper".
3. Ritentativo: `curl -s -o /dev/null -w "%{http_code}\n" --data-binary @<un wav dell'archivio> -H "Content-Type: audio/wav" -H "X-Capture-Id: cap_20260928_101010" -H "X-Capture-Ts: 2026-09-28T08:10:10Z" -H "X-Device-Id: 70041dd8263c" http://localhost:8000/captures` due volte → `201`, poi `409`.
4. Data stimata: lo stesso `curl` con `X-Capture-Id: cap_unsynced_999999` e senza `X-Capture-Ts` → la registrazione è in "Da sistemare"; correggere data e ora dalla UI a un altro giorno → WAV e sidecar spostati sul disco nella cartella giusta, badge sparito.
5. Titolo, cestino, ripristino, eliminazione definitiva dalla UI; controllare ogni volta i file sul disco.
6. Filtro per dispositivo e navigazione da telefono in LAN (`http://192.168.1.28:8000`): albero a scomparsa, righe leggibili, player usabile.
7. OTA: in `firmware/`, `echo 0.6.3 > version.txt`, `. ~/esp/esp-idf/export.sh && idf.py build`, poi `docker compose cp ../firmware/build/secondbrain_fw.bin app:/tmp/fw.bin` e `docker compose exec app secondbrain firmware publish /tmp/fw.bin --version 0.6.3`. Alla registrazione successiva il device si aggiorna (log `GET /firmware/...` nel servizio); a quella dopo la pagina Dispositivi mostra firmware 0.6.3. Infine `git checkout firmware/version.txt`.
8. Rescan: `docker compose exec postgres psql -U secondbrain -c "delete from captures"`, `docker compose exec app secondbrain rescan` → "aggiunte N" con N uguale alle registrazioni sul disco; la UI torna identica (titoli compresi).
9. `docker compose restart app` e `docker compose down && docker compose up -d`: dati, login e dispositivi restano.

Se un punto fallisce: correzione con test che riproduce il caso, nello stesso commit o nel successivo con il perché nel messaggio.

- [ ] **Step 5: Commit**

```bash
git add backend/Dockerfile backend/docker-entrypoint.sh backend/docker-compose.yml backend/.env.example backend/.dockerignore backend/README.md README.md
git commit -m "backend: Docker compose con Postgres e tunnel opzionale, README d'installazione

Stesso compose per il Mac, la ZimaBoard o un NAS: dati in DATA_DIR, configurazione
in .env, migrazioni a ogni avvio, cloudflared solo con il profilo tunnel. Verificato
sul Mac con il device vero: <elenco dei punti del Task 13 passati, con eventuali
note>."
```

(Il corpo del commit riporta l'esito reale della verifica.)

---

### Task 14: Deploy sulla ZimaBoard con il tunnel, chiusura della fase

**Files:**
- Modify: `backend/README.md` (note emerse sul tunnel, se ce ne sono)
- Modify: `docs/specs/2026-09-28-backend-archivio-design.md` ("Stato"), `CLAUDE.md` (Stato, Storico, Prossima sessione)

**Interfaces:**
- Consumes: tutto il servizio.
- Produces: servizio in produzione sulla ZimaBoard, raggiungibile in LAN e da `https://<ui>.<dominio>`; documentazione aggiornata.

- [ ] **Step 1: Dati da chiedere all'autore prima di iniziare**

Dominio gestito su Cloudflare e i due hostname da usare (proposta: `brain.<dominio>` per la UI, `ingest.<dominio>` per i dispositivi); accesso SSH alla ZimaBoard (utente e host) e directory di installazione (proposta: `~/secondbrain`); dove tenere `DATA_DIR` (disco dati della ZimaBoard).

- [ ] **Step 2: Installazione sulla ZimaBoard**

Run (dal Mac, nella radice del repo):
```bash
rsync -a --delete --exclude .venv --exclude data --exclude .env backend/ <utente>@<zimaboard>:~/secondbrain/
ssh <utente>@<zimaboard>
cd ~/secondbrain && cp .env.example .env
```
In `.env`: `POSTGRES_PASSWORD` generata con `openssl rand -base64 24`, `DATA_DIR` sul disco dati, `ALLOW_UNAUTHENTICATED_LAN=true` (il firmware attuale non manda token; dal tunnel resta obbligatorio), `DEVICE_HOSTNAME=ingest.<dominio>`.

- [ ] **Step 3: Tunnel Cloudflare**

Nella dashboard Cloudflare: Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared, nome `secondbrain`; copiare il token in `TUNNEL_TOKEN` nel `.env`. Public hostname: `brain.<dominio>` → servizio `HTTP`, URL `app:8000`; `ingest.<dominio>` → `HTTP`, `app:8000`.

Run:
```bash
docker compose --profile tunnel up -d --build
docker compose ps
docker compose logs cloudflared | grep -i "registered tunnel connection"
docker compose exec app secondbrain set-password
docker compose exec app secondbrain device add 70041dd8263c --name e-paper
```
Expected: tre servizi attivi (`app` e `postgres` healthy), almeno una connessione del tunnel registrata, token del device stampato: salvarlo in un password manager (servirà nella spec firmware "HTTPS + token").

- [ ] **Step 4: Verifica di produzione**

Con `TOKEN` = token del device e `WAV` = un file dell'archivio del Mac:
1. Da telefono su rete mobile: `https://brain.<dominio>/` → pagina di login su HTTPS; login; archivio vuoto; uscita.
2. 5 password sbagliate → "Troppi tentativi falliti" (429); dopo 15 minuti si entra.
3. `curl -s -o /dev/null -w "%{http_code}\n" https://ingest.<dominio>/healthz` → `404`; `https://ingest.<dominio>/login` → `404`.
4. Upload senza token dal tunnel: `curl ... --data-binary @$WAV -H "X-Capture-Id: cap_20260928_120000" -H "X-Capture-Ts: 2026-09-28T10:00:00Z" -H "X-Device-Id: 70041dd8263c" -H "Content-Type: audio/wav" https://ingest.<dominio>/captures` → `401`; con `-H "Authorization: Bearer $TOKEN"` → `201`; ripetuto → `409`. La registrazione compare nella UI da telefono.
5. Lo stesso upload con `-A "ESP32 HTTP Client/1.0"` e un nuovo `X-Capture-Id` → `201`. Se invece arriva `403` con una pagina HTML di Cloudflare, è Bot Fight Mode: Security → Bots → disattivarlo per il dominio, ripetere, e annotarlo nel README (sezione tunnel) e nello Stato della spec.
6. OTA dal tunnel: `curl -H "Authorization: Bearer $TOKEN" https://ingest.<dominio>/firmware/manifest.json` → `404` finché non si pubblica; pubblicata una release (Step 4 punto 7 del Task 13, stesso binario 0.6.2 del device) il manifest riporta `"url": "https://ingest.<dominio>/firmware/epaper154/secondbrain-0.6.2.bin"` e il binario si scarica con il token.
7. In LAN: `http://<ip-zimaboard>:8000/` risponde come dal tunnel.
8. Riavvio della ZimaBoard (`sudo reboot`): al ritorno servizi attivi senza intervento, UI e tunnel raggiungibili.
9. Backup: il `pg_dump` del README produce un file non vuoto; cestinare ed eliminare la registrazione di prova dalla UI.

10. Device vero sulla ZimaBoard dalla LAN di casa: in `firmware/main/secrets.h` (solo locale) base URL della rete di casa e `OTA_MANIFEST_URL` verso `http://<ip-zimaboard>:8000`; build e flash (DEV mode con il cavo dati, oppure push `POST /ota`). Una registrazione con PWR → compare nella UI da telefono via tunnel, il dispositivo in "Dispositivi" risulta visto adesso. Il Mac di sviluppo non serve più come server; `tools/capture_server.py` resta nel repo per i test del firmware.

- [ ] **Step 5: Documentazione e commit di chiusura**

- Spec, riga "Stato": "implementato e verificato il <data> (branch `backend-fase1b`, piano `docs/plans/2026-09-28-backend-archivio.md`); in produzione sulla ZimaBoard dietro tunnel Cloudflare", più le deviazioni emerse (per esempio Bot Fight Mode).
- `CLAUDE.md`: aggiornare "Stato" (backend in produzione, device in LAN verso la ZimaBoard senza token), "Storico" (voce della giornata con ciò che è emerso), "Prossima sessione" (spec firmware "HTTPS + token", §11 della spec del backend: prima il trattamento di 401/403/429 come errori temporanei), regole (prefisso di commit `backend:`, comando dei test backend).
- `backend/README.md`: eventuali note sul tunnel emerse.

```bash
git add docs/specs/2026-09-28-backend-archivio-design.md CLAUDE.md backend/README.md
git commit -m "docs: backend Fase 1b verificato in produzione sulla ZimaBoard

<esito dei punti di verifica del Task 14 e deviazioni emerse>. Il device carica gia'
sulla ZimaBoard dalla LAN; da fuori casa servira' la spec firmware HTTPS + token."
```
