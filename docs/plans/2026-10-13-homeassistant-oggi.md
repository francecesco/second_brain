# Home Assistant e schermata "Oggi" — Piano di implementazione

> Esecuzione task per task, TDD. Le checkbox non si spuntano: lo stato si legge dai commit e
> dallo "Stato" della spec.

**Obiettivo:** il backend legge calendari, sensori e lista della spesa da Home Assistant
(configurato dalle Impostazioni) e disegna un'immagine 1 bit che il device mostra nella
schermata "Oggi", tra la sua intestazione e il suo piede.

**Architettura:** connettore `homeassistant.py` (httpx, solo lettura) → istantanea nella
tabella delle impostazioni, aggiornata dal worker ogni 5 minuti → `digest.py` (Pillow)
disegna 200×136 px → `GET /digest/today` con ETag/304/204 → il firmware (`digest.c`) la
scarica, la salva su SD e `screen.c` la compone con intestazione e piede; ridisegno solo se
cambia lo stato o l'etag.

**Stack:** FastAPI, SQLAlchemy/Postgres, httpx, Pillow, Jinja/htmx; ESP-IDF 5.3.1 (C),
unity sull'host.

**Spec:** `docs/specs/2026-10-13-homeassistant-oggi-design.md`

## Vincoli globali

- Test backend: `cd backend && .venv/bin/pytest -q` (Postgres di test su, `docker compose -f
  docker-compose.test.yml up -d`); 604 verdi alla partenza, devono restare verdi. Mai due
  pytest insieme (DB condiviso).
- Test firmware sull'host: `cd firmware/host_test && . ~/esp/esp-idf/export.sh >/dev/null &&
  idf.py build && timeout 10 ./build/host_test.elf` (43 verdi alla partenza). Build del
  firmware senza warning: `cd firmware && idf.py build`.
- Immagine: **200×136** px, 1 bit, bit 1 = nero, righe impacchettate MSB first, **3.400
  byte**; nel device la zona va da y = **30** a y = 165; piede da 166 a 199.
- `HA_REFRESH_S = 300`; dati vecchi oltre **30 minuti** → riga `agg. HH:MM`; timeout delle
  chiamate a Home Assistant **10 s**; Home Assistant minimo 2023.12 (`return_response`).
- Chiavi delle impostazioni: `homeassistant` (config), `homeassistant_token` (cifrato),
  `homeassistant_snapshot`, `homeassistant_refresh` (richiesta di aggiornamento).
- Il token di Home Assistant non compare mai in pagine, log, eccezioni mostrate o test con
  valori realistici (usare stringhe palesemente finte).
- Firmware: costanti in `config.h`, buffer ≥ 1 KB sull'heap, funzioni pure nei `*_policy.c`
  con test host, ritorni `esp_err_t`, niente `ESP_ERROR_CHECK` nel flusso normale. Il font del
  device è solo ASCII: nel piede il separatore è ` | `.
- Commit in italiano con prefisso `backend:`/`firmware:`/`docs:`, firmati solo dall'autore,
  nessun riferimento all'assistente; non toccare `main/secrets.h`; `version.txt` resta 0.1.0
  in git.
- UI secondo `docs/stile-ui.md` (taglia unica dei tasti, un solo tasto pieno per vista).
- Deploy sulla ZimaBoard solo dopo l'ok dell'autore.

## Punti da sorvegliare

- Home Assistant restituisce le date degli eventi in due forme: data (`2026-10-13`, tutto il
  giorno, fine esclusa) e data-ora con fuso; un evento di tutto il giorno non deve finire in
  "domani" per un errore di fuso → test in Task 5.
- Un calendario scelto e poi cancellato in Home Assistant non deve far fallire tutta la
  lettura (`calendar.get_events` con un'entità inesistente dà errore) → filtro con
  `/api/states` e test in Task 1.
- Un errore di Home Assistant non deve cancellare l'istantanea buona → test in Task 3.
- L'immagine non deve cambiare etag a ogni lettura se il contenuto è uguale (altrimenti il
  device ridisegna ogni 5 minuti): l'etag è l'hash dei byte, e la riga `agg.` compare solo
  oltre 30 minuti → test in Task 4 e 5.
- Un `digest.bin` troncato sulla SD (spegnimento a metà scrittura) non deve finire sullo
  schermo → scrittura su file temporaneo + rename e controllo della dimensione, test host in
  Task 8.

---

### Task 1: Connettore Home Assistant (`homeassistant.py`)

**File:** crea `backend/secondbrain/homeassistant.py`, `backend/tests/test_homeassistant.py`.

**Interfacce prodotte:**

```python
TIMEOUT_S = 10.0

class HaError(Exception):
    """Messaggio da mostrare all'utente; mai il token."""

@dataclass(frozen=True)
class SensorChoice:
    entity_id: str
    label: str | None = None

@dataclass(frozen=True)
class HaConfig:
    url: str
    calendars: tuple[str, ...] = ()
    sensors: tuple[SensorChoice, ...] = ()
    shopping_list: str | None = None

@dataclass(frozen=True)
class Entity:
    entity_id: str
    name: str          # friendly_name, o l'entity_id

@dataclass(frozen=True)
class Event:
    calendar: str      # entity_id
    summary: str
    start: str         # come arriva da HA: "2026-10-13" o "2026-10-13T09:30:00+02:00"
    end: str
    all_day: bool

@dataclass(frozen=True)
class SensorValue:
    entity_id: str
    label: str
    state: str
    unit: str | None

@dataclass(frozen=True)
class Reading:
    events: tuple[Event, ...]
    shopping: tuple[str, ...]
    sensors: tuple[SensorValue, ...]
    missing: tuple[str, ...]   # entità scelte che HA non elenca più

def clean_url(raw: str) -> str            # strip, senza "/" finale; HaError se non http(s)
def test(client: httpx.Client, url: str, token: str) -> str          # "API running." o HaError
def list_entities(client, url, token) -> dict[str, list[Entity]]    # chiavi "calendar", "sensor", "todo", ordinate per nome
def read(client, config: HaConfig, token: str, start: datetime, end: datetime) -> Reading
```

- `test`: `GET {url}/api/` con `Authorization: Bearer`; 200 con `message` → il messaggio.
- `list_entities`: `GET {url}/api/states`; tiene i domini `calendar`, `sensor`, `todo`.
- `read`:
  1. `GET /api/states` una volta: sensori scelti (stato e `unit_of_measurement`; etichetta =
     `label` o `friendly_name`) e insieme delle entità esistenti;
  2. calendari scelti **ed esistenti**: `POST /api/services/calendar/get_events?return_response`
     con `{"entity_id": [...], "start_date_time": start.isoformat(), "end_date_time":
     end.isoformat()}`; risposta `{"service_response": {entity: {"events": [...]}}}`; un
     evento con `start` di 10 caratteri è `all_day`;
  3. lista (se scelta ed esistente): `POST /api/services/todo/get_items?return_response` con
     `{"entity_id": X, "status": ["needs_action"]}` → i `summary` degli `items`;
  4. scelte non esistenti → `missing`.
- Errori → `HaError` con testo italiano: 401/403 "token rifiutato da Home Assistant (401)";
  altri ≥ 400 "Home Assistant ha risposto <status>"; `httpx.TimeoutException` "Home Assistant
  non risponde (timeout)"; `httpx.ConnectError` "Home Assistant irraggiungibile"; errore SSL
  "certificato di Home Assistant non valido"; JSON di una chiamata con `return_response` senza
  `service_response` (o non oggetto) "serve Home Assistant 2023.12 o successivo". Il messaggio
  non contiene mai il token né gli header.
- Timeout `TIMEOUT_S` su ogni chiamata (`timeout=` della request).

- [ ] **Step 1: test che falliscono** (`tests/test_homeassistant.py`), con un Home Assistant
  finto su `httpx.MockTransport`:

```python
import httpx
import pytest
from datetime import datetime, timezone

from secondbrain import homeassistant as ha

TOKEN = "token-finto-per-i-test"
URL = "http://ha.test:8123"
STATES = [
    {"entity_id": "calendar.casa", "state": "off", "attributes": {"friendly_name": "Casa"}},
    {"entity_id": "calendar.differenziata", "state": "off", "attributes": {"friendly_name": "Raccolta"}},
    {"entity_id": "sensor.t_soggiorno", "state": "21.5",
     "attributes": {"friendly_name": "Temperatura soggiorno", "unit_of_measurement": "°C"}},
    {"entity_id": "todo.shopping_list", "state": "3", "attributes": {"friendly_name": "Spesa"}},
    {"entity_id": "light.cucina", "state": "on", "attributes": {}},
]


def fake(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def hass(request: httpx.Request) -> httpx.Response:
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    path = request.url.path
    if path == "/api/":
        return httpx.Response(200, json={"message": "API running."})
    if path == "/api/states":
        return httpx.Response(200, json=STATES)
    if path == "/api/services/calendar/get_events":
        assert "return_response" in request.url.query.decode()
        body = __import__("json").loads(request.content)
        assert body["entity_id"] == ["calendar.casa"]
        return httpx.Response(200, json={"changed_states": [], "service_response": {
            "calendar.casa": {"events": [
                {"start": "2026-10-13T09:30:00+02:00", "end": "2026-10-13T10:30:00+02:00", "summary": "Riunione"},
                {"start": "2026-10-14", "end": "2026-10-15", "summary": "Carta"}]}}})
    if path == "/api/services/todo/get_items":
        return httpx.Response(200, json={"changed_states": [], "service_response": {
            "todo.shopping_list": {"items": [{"summary": "latte", "uid": "1", "status": "needs_action"},
                                             {"summary": "pane", "uid": "2", "status": "needs_action"}]}}})
    return httpx.Response(404)


START = datetime(2026, 10, 13, 6, 0, tzinfo=timezone.utc)
END = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
CONFIG = ha.HaConfig(url=URL, calendars=("calendar.casa", "calendar.sparito"),
                     sensors=(ha.SensorChoice("sensor.t_soggiorno", "casa"), ha.SensorChoice("sensor.sparito")),
                     shopping_list="todo.shopping_list")


def test_clean_url():
    assert ha.clean_url(" http://ha.test:8123/ ") == "http://ha.test:8123"
    with pytest.raises(ha.HaError):
        ha.clean_url("ftp://ha.test")
    with pytest.raises(ha.HaError):
        ha.clean_url("")


def test_test_ok():
    assert ha.test(fake(hass), URL, TOKEN) == "API running."


def test_test_401_never_shows_the_token():
    with pytest.raises(ha.HaError) as exc:
        ha.test(fake(lambda r: httpx.Response(401)), URL, TOKEN)
    assert "401" in str(exc.value) and TOKEN not in str(exc.value)


def test_test_timeout_and_unreachable():
    def slow(request):
        raise httpx.ReadTimeout("lento", request=request)
    def down(request):
        raise httpx.ConnectError("giù", request=request)
    with pytest.raises(ha.HaError, match="timeout"):
        ha.test(fake(slow), URL, TOKEN)
    with pytest.raises(ha.HaError, match="irraggiungibile"):
        ha.test(fake(down), URL, TOKEN)


def test_list_entities_groups_by_domain():
    found = ha.list_entities(fake(hass), URL, TOKEN)
    assert [e.entity_id for e in found["calendar"]] == ["calendar.casa", "calendar.differenziata"]
    assert found["sensor"][0].name == "Temperatura soggiorno"
    assert [e.entity_id for e in found["todo"]] == ["todo.shopping_list"]
    assert set(found) == {"calendar", "sensor", "todo"}


def test_read_skips_missing_entities():
    reading = ha.read(fake(hass), CONFIG, TOKEN, START, END)
    assert [e.summary for e in reading.events] == ["Riunione", "Carta"]
    assert [e.all_day for e in reading.events] == [False, True]
    assert reading.shopping == ("latte", "pane")
    assert reading.sensors == (ha.SensorValue("sensor.t_soggiorno", "casa", "21.5", "°C"),)
    assert set(reading.missing) == {"calendar.sparito", "sensor.sparito"}


def test_read_old_home_assistant_without_service_response():
    def old(request):
        if request.url.path == "/api/states":
            return httpx.Response(200, json=STATES)
        return httpx.Response(200, json=[])     # vecchio formato: lista di stati cambiati
    with pytest.raises(ha.HaError, match="2023.12"):
        ha.read(fake(old), CONFIG, TOKEN, START, END)


def test_read_without_choices_makes_no_service_calls():
    calls = []
    def spy(request):
        calls.append(request.url.path)
        return hass(request)
    reading = ha.read(fake(spy), ha.HaConfig(url=URL), TOKEN, START, END)
    assert calls == ["/api/states"] and reading == ha.Reading((), (), (), ())
```

- [ ] **Step 2:** `.venv/bin/pytest tests/test_homeassistant.py -q` → FAIL (modulo mancante).
- [ ] **Step 3:** implementare il modulo secondo le interfacce sopra (docstring in italiano;
  un helper `_request(client, method, url, token, json=None, params=None)` che traduce le
  eccezioni httpx e gli status in `HaError`; un helper `_service_response(data)` che
  controlla `service_response`).
- [ ] **Step 4:** test verdi, suite completa verde.
- [ ] **Step 5: commit** `backend: connettore Home Assistant in sola lettura`.

---

### Task 2: Configurazione, token e istantanea (`ha_store.py`)

**File:** crea `backend/secondbrain/ha_store.py`, `backend/tests/test_ha_store.py`.

**Interfacce consumate:** `settings_store.get_setting/set_setting`, `SecretBox`,
`mask_key`, `NoSettingsKey`; `homeassistant.HaConfig/SensorChoice/Reading/Event/SensorValue`.

**Interfacce prodotte:**

```python
CONFIG_KEY = "homeassistant"
TOKEN_KEY = "homeassistant_token"
SNAPSHOT_KEY = "homeassistant_snapshot"
REFRESH_KEY = "homeassistant_refresh"
MAX_LABEL_LEN = 24

@dataclass(frozen=True)
class Snapshot:
    fetched_at: datetime | None          # ultima lettura riuscita
    reading: Reading | None
    error: str | None = None             # ultimo errore (None dopo una lettura riuscita)
    error_at: datetime | None = None

def get_config(s) -> HaConfig | None                     # None se manca l'URL
def save_connection(s, box, url: str, token: str | None) -> None   # token None/"" = non cambiarlo; clean_url
def save_choices(s, calendars, sensors: list[SensorChoice], shopping_list: str | None) -> None
def clear(s) -> None                                     # toglie config, token, istantanea
def get_token(s, box) -> str | None                      # None se assente o illeggibile
def token_status(s, box) -> str                          # mask_key(...) / NO_KEY / UNREADABLE_KEY
def get_snapshot(s) -> Snapshot                          # Snapshot(None, None) se assente o rotto
def save_reading(s, reading: Reading, now: datetime) -> None   # azzera l'errore
def save_error(s, error: str, now: datetime) -> None           # conserva reading e fetched_at
def request_refresh(s, now: datetime) -> None
def refresh_requested_after(s, when: datetime | None) -> bool  # True se c'è una richiesta più recente di `when`
```

- Le etichette dei sensori si ripuliscono (strip, una riga, al massimo `MAX_LABEL_LEN`; vuota
  → None). `save_connection` con `box.available` falso e un token nuovo → `NoSettingsKey`.
- Serializzazione JSON esplicita (`dataclasses.asdict` + datetime ISO); `get_snapshot` non
  solleva mai su dati rotti.

- [ ] **Step 1: test che falliscono** (`tests/test_ha_store.py`, fixture `db` e
  `SecretBox(TEST_SETTINGS_KEY)` come in `test_settings_store.py`):

```python
from datetime import datetime, timezone

import pytest

from secondbrain import ha_store
from secondbrain import homeassistant as ha
from secondbrain import settings_store as store
from tests.helpers import NOW, TEST_SETTINGS_KEY

BOX = store.SecretBox(TEST_SETTINGS_KEY)
READING = ha.Reading(events=(ha.Event("calendar.casa", "Riunione", "2026-10-13T09:30:00+02:00",
                                      "2026-10-13T10:30:00+02:00", False),),
                     shopping=("latte",), sensors=(ha.SensorValue("sensor.t", "casa", "21.5", "°C"),),
                     missing=())


def test_connection_and_token_are_saved_encrypted(db):
    ha_store.save_connection(db, BOX, " http://ha.test:8123/ ", "token-finto-per-i-test")
    db.commit()
    assert ha_store.get_config(db).url == "http://ha.test:8123"
    raw = store.get_setting(db, ha_store.TOKEN_KEY, None)
    assert raw and "token-finto" not in raw
    assert ha_store.get_token(db, BOX) == "token-finto-per-i-test"
    assert "token-finto-per-i-test" not in ha_store.token_status(db, BOX)


def test_empty_token_keeps_the_old_one(db):
    ha_store.save_connection(db, BOX, "http://ha.test:8123", "token-finto-per-i-test")
    ha_store.save_connection(db, BOX, "http://altro.test:8123", "")
    assert ha_store.get_token(db, BOX) == "token-finto-per-i-test"
    assert ha_store.get_config(db).url == "http://altro.test:8123"


def test_without_settings_key_the_token_is_refused(db):
    with pytest.raises(store.NoSettingsKey):
        ha_store.save_connection(db, store.SecretBox(None), "http://ha.test:8123", "token-finto")


def test_choices_and_labels(db):
    ha_store.save_connection(db, BOX, "http://ha.test:8123", "token-finto")
    ha_store.save_choices(db, ["calendar.casa"], [ha.SensorChoice("sensor.t", "  casa\n "),
                                                 ha.SensorChoice("sensor.u", "")], "todo.spesa")
    cfg = ha_store.get_config(db)
    assert cfg.calendars == ("calendar.casa",)
    assert cfg.sensors == (ha.SensorChoice("sensor.t", "casa"), ha.SensorChoice("sensor.u", None))
    assert cfg.shopping_list == "todo.spesa"


def test_snapshot_roundtrip_and_error_keeps_data(db):
    assert ha_store.get_snapshot(db) == ha_store.Snapshot(None, None)
    ha_store.save_reading(db, READING, NOW)
    later = datetime(2026, 9, 28, 12, 5, tzinfo=timezone.utc)
    ha_store.save_error(db, "Home Assistant non risponde (timeout)", later)
    snap = ha_store.get_snapshot(db)
    assert snap.reading == READING and snap.fetched_at == NOW
    assert (snap.error, snap.error_at) == ("Home Assistant non risponde (timeout)", later)
    ha_store.save_reading(db, READING, later)
    assert ha_store.get_snapshot(db).error is None


def test_broken_snapshot_reads_as_empty(db):
    store.set_setting(db, ha_store.SNAPSHOT_KEY, {"fetched_at": "rotto", "reading": 3})
    assert ha_store.get_snapshot(db) == ha_store.Snapshot(None, None)


def test_refresh_request(db):
    assert not ha_store.refresh_requested_after(db, None)
    ha_store.request_refresh(db, NOW)
    assert ha_store.refresh_requested_after(db, None)
    assert not ha_store.refresh_requested_after(db, NOW)


def test_clear(db):
    ha_store.save_connection(db, BOX, "http://ha.test:8123", "token-finto")
    ha_store.save_reading(db, READING, NOW)
    ha_store.clear(db)
    assert ha_store.get_config(db) is None and ha_store.get_token(db, BOX) is None
    assert ha_store.get_snapshot(db).reading is None
```

  Verificare i nomi reali in `settings_store` (`NO_KEY`, `UNREADABLE_KEY`, `mask_key`,
  `NoSettingsKey`, e se esiste una funzione per cancellare un'impostazione; altrimenti
  `set_setting(..., None)` e `get_*` che trattano `None` come assente).

- [ ] **Step 2:** FAIL. **Step 3:** implementare. **Step 4:** verde, suite verde.
- [ ] **Step 5: commit** `backend: configurazione e istantanea di Home Assistant`.

---

### Task 3: Lettura periodica nel worker

**File:** modifica `backend/secondbrain/worker.py`, `backend/secondbrain/cli.py`;
crea `backend/tests/test_ha_refresh.py`.

**Interfacce consumate:** `ha_store.*`, `homeassistant.read`, `HaError`.

**Interfacce prodotte:**
- `HA_REFRESH_S = 300` in `worker.py`.
- `Worker.http: httpx.Client | None = None` (campo nuovo, ultimo) e
  `Worker.maybe_refresh_homeassistant() -> bool`: legge se c'è una configurazione con token e
  (mai letto da questo worker, oppure passati `HA_REFRESH_S`, oppure
  `refresh_requested_after(self._last_ha_refresh)`); finestra di lettura da `now` a fine di
  domani nel fuso `self.tz`; successo → `save_reading`; `HaError` → `save_error` (e log senza
  token); ritorna True se ha provato a leggere. Senza `self.http` o senza config: False.
- `run_forever` chiama `worker.maybe_refresh_homeassistant()` a ogni giro (non solo da fermo),
  con `except Exception` che logga e continua.
- `cli.py`: passa `http=client` al Worker.

- [ ] **Step 1: test che falliscono** (`tests/test_ha_refresh.py`): Worker costruito come in
  `tests/test_worker.py` (fixture `env` o equivalente minimale) con `http=fake(hass)` (riusare
  `hass`, `TOKEN`, `URL` importandoli da `tests.test_homeassistant`) e `clock` finto:
  1. senza configurazione → False, nessuna istantanea;
  2. con configurazione e token → True, `get_snapshot().reading.shopping == ("latte", "pane")`;
  3. subito dopo → False (intervallo); avanzando il clock di 301 s → True;
  4. `request_refresh` dopo l'ultima lettura → True anche prima dei 300 s;
  5. Home Assistant che dà 500 → True, istantanea precedente intatta, `error` valorizzato;
  6. finestra: il `end_date_time` richiesto è la mezzanotte di dopodomani nel fuso di Roma
     (catturarlo nell'handler finto).
- [ ] **Step 2:** FAIL. **Step 3:** implementare. **Step 4:** verde, suite verde.
- [ ] **Step 5: commit** `backend: il worker legge Home Assistant ogni 5 minuti`.

---

### Task 4: Disegno dell'immagine (`digest.py`)

**File:** crea `backend/secondbrain/digest.py`, `backend/secondbrain/assets/SpaceGrotesk-Bold.ttf`
(+ `backend/secondbrain/assets/space-grotesk-OFL.txt`), `backend/tools/make_digest_font.py`,
`backend/tests/test_digest.py`; modifica `backend/pyproject.toml` (+ `uv.lock`):
`pillow` nelle dipendenze, `fonttools[woff]` nel gruppo dev.

**Interfacce consumate:** `ha_store.Snapshot`, `homeassistant.Event/SensorValue`.

**Interfacce prodotte:**

```python
WIDTH, HEIGHT = 200, 136
PACKED_BYTES = WIDTH * HEIGHT // 8      # 3400
STALE_AFTER = timedelta(minutes=30)
MAX_TODAY, MAX_TOMORROW = 3, 2

def lines(snapshot: Snapshot, now: datetime, tz: ZoneInfo, *, shopping: bool) -> list[tuple[str, str]]
    # [(stile, testo)], stile in {"head", "item", "small"}: il contenuto prima dell'impaginazione;
    # shopping = c'è una lista scelta nella configurazione (config.shopping_list is not None)
def render(snapshot: Snapshot, now: datetime, tz: ZoneInfo, *, shopping: bool) -> Image.Image   # modo "1", 200x136
def pack(image: Image.Image) -> bytes      # bit 1 = nero, MSB first, righe di 25 byte
def etag(data: bytes) -> str               # '"' + sha256(data)[:16] + '"'
def preview_png(image: Image.Image, scale: int = 2) -> bytes
```

- **Font**: `tools/make_digest_font.py` converte una volta `secondbrain/web/static/space-grotesk.woff2`
  in TTF con fontTools; se il font è variabile, istanza `wght=700` con
  `fontTools.varLib.instancer`. Il TTF e la licenza OFL (copia di
  `static/space-grotesk-OFL.txt`) si committano; lo script resta per rifarlo.
- **Contenuto (`lines`)**, nel fuso `tz`:
  - `("head", "OGGI")` + eventi non finiti di oggi (tutto il giorno: data di inizio ≤ oggi <
    data di fine; a orario: fine > now e inizio < fine di oggi), prima quelli di tutto il
    giorno, poi per ora; massimo `MAX_TODAY`; testo `"HH:MM Titolo"` o `"Titolo"` per tutto il
    giorno. Se non ce ne sono: `("item", "niente in programma")`.
  - `("head", "DOMANI")` + eventi di domani (stesse regole), massimo `MAX_TOMORROW`; la sezione
    si omette se vuota.
  - `("head", "SPESA")` + `("item", "N articoli")` / `"1 articolo"` / `"vuota"`; omessa se
    `shopping` è falso (nessuna lista scelta).
  - sensori: `"etichetta valore unità"` con il valore numerico arrotondato a 1 decimale e la
    virgola (`21,5°C`), due per riga separati da `" · "`.
  - se `now - fetched_at > STALE_AFTER`: ultima riga `("small", "agg. HH:MM")` (ora locale).
- **Impaginazione (`render`)**: `head` 12 px, `item` 16 px, `small` 11 px (dimensioni del TTF;
  regolabili guardando l'anteprima), interlinea fissa, margine sinistro 4 px; testo disegnato
  con `ImageDraw` su immagine modo `"1"` (`fontmode = "1"`, niente sfumature); titoli più
  larghi di 192 px tagliati con "…" misurando con `textlength`; le righe che non stanno si
  tolgono dal fondo del contenuto, ma la riga `small` resta sempre (si riserva il suo spazio).
- `reading` assente → `render` non viene chiamato (lo decide l'endpoint, Task 5).

- [ ] **Step 1: test che falliscono** (`tests/test_digest.py`), con `now =
  2026-10-13 08:00 UTC` (10:00 a Roma) e `tz = Europe/Rome`:
  1. `lines`: eventi ordinati (tutto il giorno prima), già finiti esclusi, massimo 3, domani
     con l'evento di tutto il giorno "Carta" (`2026-10-14`/`2026-10-15`) e non in oggi;
  2. evento a cavallo della mezzanotte (`23:30`→`00:30`) in oggi; evento di tutto il giorno
     di ieri (`2026-10-12`/`2026-10-13`) escluso;
  3. spesa: 0 → `vuota`, 1 → `1 articolo`, 5 → `5 articoli`; `shopping=False` → nessuna
     sezione SPESA;
  4. sensori: `"21.5"`+`°C` → `casa 21,5°C`; stato non numerico (`"unavailable"`) mostrato così;
     tre sensori → due righe;
  5. `agg. HH:MM` solo oltre 30 minuti (29 min no, 31 sì);
  6. `render` → modo `"1"`, size `(200, 136)`; `pack` → 3.400 byte; un'immagine tutta bianca →
     tutti zero, un pixel nero in (0,0) → primo byte `0x80`, in (8,0) → secondo byte `0x80`;
  7. titolo lunghissimo: nessun pixel nero oltre x = 196 (taglio con "…");
  8. stessa istantanea due volte → stesso `etag`; un articolo in più → etag diverso;
  9. molte righe (10 eventi, 6 sensori, dati vecchi): l'immagine contiene la riga `agg.`
     (l'ultima fascia di 12 px non è vuota) e non solleva.
- [ ] **Step 2:** FAIL. **Step 3:** script del font, TTF, dipendenze (`uv add pillow`,
  `uv add --dev "fonttools[woff]"`), implementazione. **Step 4:** verde, suite verde.
- [ ] **Step 5:** anteprima PNG (`preview_png`) salvata nello scratch e guardata; regolare
  dimensioni dei caratteri se il testo è troppo piccolo o grande per 200 px.
- [ ] **Step 6: commit** `backend: immagine della schermata Oggi`.

---

### Task 5: Endpoint `GET /digest/today` e anteprima

**File:** crea `backend/secondbrain/digest_api.py`, `backend/tests/test_digest_api.py`;
modifica `backend/secondbrain/app.py`.

**Interfacce consumate:** `ota.require_device`, `ha_store.get_config/get_snapshot`,
`digest.render/pack/etag/preview_png`, `web.deps.require_login`.

**Interfacce prodotte:**
- `GET /digest/today` (dipendenza `require_device`): 204 se `get_config` è None o
  `snapshot.reading` è None; altrimenti `pack(render(..., shopping=config.shopping_list is
  not None))` con `media_type
  "application/octet-stream"`, header `ETag`, `Cache-Control: no-store`; se `If-None-Match`
  coincide con l'etag → 304 senza corpo.
- `GET /settings/homeassistant/preview.png` (login): `preview_png(render(...))` o 404 se non
  c'è niente da mostrare.
- Router incluso in `app.py`. Il percorso `/digest/` va aggiunto ai percorsi dei dispositivi
  se `app.py` ha una lista per `DEVICE_HOSTNAME` (`_is_device_path`).

- [ ] **Step 1: test che falliscono**: senza token → 401; con token (fixture `token`) e senza
  config → 204; con config e istantanea (`ha_store.save_connection` + `save_reading`) → 200,
  `len(body) == 3400`, `ETag` presente; stessa richiesta con `If-None-Match` → 304 e corpo
  vuoto; anteprima: senza login → redirect al login, con login (`ui`) → `image/png`.
- [ ] **Step 2:** FAIL. **Step 3:** implementare. **Step 4:** verde, suite verde.
- [ ] **Step 5: commit** `backend: endpoint della schermata Oggi per il device`.

---

### Task 6: Card "Connessione Home Assistant" nelle Impostazioni

**File:** crea `backend/secondbrain/web/homeassistant.py`,
`backend/secondbrain/web/templates/_homeassistant_card.html`,
`backend/tests/test_web_homeassistant.py`; modifica `web/templates/settings.html` (include
della card dopo "Vocabolario dei nomi"), `web/settings.py` (contesto della card),
`app.py` (router), `static/style.css` se serve (solo classi esistenti dove possibile).

**Interfacce consumate:** `ha_store.*`, `homeassistant.test/list_entities/HaError`,
`app.state.http`, `app.state.box`, `page_context`, `require_csrf`.

**Rotte** (tutte con login; i POST con CSRF; risposte "Prova"/"Carica entità" come frammenti
htmx sul modello di `probe_provider`, il resto redirect 303 a `/settings#homeassistant`):
- `POST /settings/homeassistant` (url, token): `save_connection` + `request_refresh`;
  `HaError` (URL non valido) o `NoSettingsKey` → la pagina con l'errore nella card (o redirect
  con messaggio: seguire il modello delle altre card).
- `POST /settings/homeassistant/test`: `test(...)` con URL e token salvati → frammento
  `<span class="test-result ok|ko">` (messaggio escapato).
- `GET /settings/homeassistant/entities`: frammento con i tre gruppi di caselle (form di
  scelta) costruito da `list_entities`; entità salvate non elencate → voce "non trovata"
  spuntata; errore → frammento `test-result ko`.
- `POST /settings/homeassistant/choices`: `calendars` (lista), `sensors` (lista di entity_id) +
  `label_<entity_id>` per ognuno, `shopping_list` ("" = nessuna) → `save_choices` +
  `request_refresh`.
- `POST /settings/homeassistant/refresh` → `request_refresh`.
- `POST /settings/homeassistant/delete` → `clear` (con `onsubmit confirm`).

**Card** (`_homeassistant_card.html`, `<section class="card" id="homeassistant">`):
titolo "Connessione Home Assistant"; testo breve su cosa serve (URL e token di lunga durata da
Profilo → Sicurezza in Home Assistant); form URL + token (`type=password`, vuoto, placeholder
"lascia vuoto per non cambiarlo" se c'è già) con "Salva" (`pri`) e "Prova"; stato del token
(`token_status`); avviso `SETTINGS_KEY` come per i provider; blocco "Entità" con pulsante
"Carica entità da Home Assistant" (htmx `hx-get` nel contenitore) e, se ci sono scelte salvate,
il loro riepilogo; "Ultima lettura: HH:MM · N eventi · N articoli · N sensori" o l'ultimo
errore (`redact`), pulsante "Aggiorna ora"; anteprima `<img src="/settings/homeassistant/preview.png">`
ingrandita 2× con `image-rendering: pixelated` se c'è un'istantanea; "Scollega" in fondo.

- [ ] **Step 1: test che falliscono** (`tests/test_web_homeassistant.py`, Home Assistant
  finto in `app.state.http` come fanno i test dei provider in `test_web_settings.py`):
  salvataggio (URL ripulito, token cifrato, **token mai nella pagina** né nelle risposte);
  CSRF mancante → 403; "Prova" ok e 401; caricamento entità con caselle e "non trovata";
  salvataggio delle scelte con etichette; "Aggiorna ora" crea la richiesta; "Scollega";
  senza `SETTINGS_KEY` il campo token è disabilitato e c'è l'avviso; pagina `/settings` con la
  card e l'anteprima quando c'è un'istantanea.
- [ ] **Step 2:** FAIL. **Step 3:** implementare. **Step 4:** verde, suite verde.
- [ ] **Step 5: screenshot** a 1280 e 390 px (chiaro e scuro) della card vuota, configurata
  con entità caricate e con l'anteprima, procedura di `docs/stile-ui.md`; controllo: taglia
  unica dei tasti, un solo tasto pieno per form, niente scroll orizzontale a 390 px.
- [ ] **Step 6: commit** `backend: connessione Home Assistant nelle Impostazioni`.

---

### Task 7: Firmware — decisione di ridisegno ed etag mostrato

**File:** modifica `firmware/main/sync_retry_policy.{h,c}`, `firmware/main/retry_state.{h,c}`,
`firmware/main/config.h`; test in `firmware/host_test/main/test_policies.c` (+ `RUN_TEST` in
`test_ota_manifest.c`, dove sta `app_main` dei test).

**Interfacce prodotte:**

```c
// config.h
#define SB_DIGEST_W          200
#define SB_DIGEST_H          136
#define SB_DIGEST_Y          30
#define SB_DIGEST_BYTES      (SB_DIGEST_W * SB_DIGEST_H / 8)   // 3400
#define SB_DIGEST_ETAG_MAX   48
#define SB_DIGEST_FILE       "digest.bin"
#define SB_DIGEST_ETAG_FILE  "digest.etag"
#define SB_DIGEST_TIMEOUT_MS 8000

// sync_retry_policy.h
// Ridisegno a un wake da timer: stato cambiato (status_should_refresh) oppure immagine
// diversa da quella mostrata. NULL vale "nessuna immagine".
bool today_should_refresh(bool status_changed, const char *etag_now, const char *etag_shown);
// Un file dell'immagine e' utilizzabile solo con la dimensione esatta.
bool digest_size_valid(long size);

// retry_state.h
const char *retry_state_digest_shown(void);          // "" se nessuna
void        retry_state_set_digest_shown(const char *etag);   // NULL = nessuna
```

- [ ] **Step 1: test host che falliscono**: `today_should_refresh(false, "\"a\"", "\"a\"")` →
  false; `(false, "\"b\"", "\"a\"")` → true; `(true, X, X)` → true; `(false, NULL, "")` →
  false; `(false, "\"a\"", "")` → true; `(false, NULL, "\"a\"")` → true.
  `digest_size_valid(3400)` → true; 0, 3399, 3401, -1 → false.
- [ ] **Step 2:** build dei test host → FAIL (simboli mancanti). **Step 3:** implementare
  (`retry_state.c`: `RTC_DATA_ATTR static char s_digest[SB_DIGEST_ETAG_MAX]`, copia con
  `strlcpy`). **Step 4:** test host verdi (tutti), `idf.py build` senza warning.
- [ ] **Step 5: commit** `firmware: decisione di ridisegno con l'immagine della schermata Oggi`.

---

### Task 8: Firmware — composizione `screen_render_today` e anteprima

**File:** modifica `firmware/main/screen.{h,c}`, `firmware/tools/screen_preview.c`; test in
`firmware/host_test/main/test_screen.c` (+ `RUN_TEST`).

**Interfacce prodotte:**

```c
// screen.h
typedef struct { ... esistenti ...; bool alert; } screen_status_t;   // alert in fondo: gli
                                                                    // inizializzatori posizionali restano validi
// Schermata "Oggi": intestazione come screen_render_status (ora e batteria), immagine del
// backend (SB_DIGEST_W x SB_DIGEST_H, formato del framebuffer) da y = SB_DIGEST_Y, piede su
// una riga "coda N | rete | esito" (rete "no wifi" se NULL), in negativo se alert.
void screen_render_today(uint8_t *fb, const screen_status_t *st, const uint8_t *digest);
```

- `screen.c` non include `config.h`? Se non lo include, definire in `screen.h` le stesse
  misure (`SCREEN_DIGEST_Y 30`, `SCREEN_DIGEST_H 136`) e in `config.h` controllarle con
  `_Static_assert` contro `SB_DIGEST_*`; scegliere la via che non porta ESP-IDF nell'host.
- L'intestazione riusa il codice di `screen_render_status` (estrarre una funzione statica
  `draw_bar` se serve). Piede: `font_grotesk_16`, testo centrato verticalmente nei 34 px, tagliato
  a larghezza con `screen_text_width`; con `alert` fascia nera e testo bianco come l'esito di
  oggi.

- [ ] **Step 1: test host che falliscono**: con un `digest` tutto `0xFF`, le righe da 30 a
  165 sono tutte `0xFF` e la riga 0 è bianca; con `alert = true` l'ultima riga è tutta nera,
  con `alert = false` non lo è; `digest` tutto `0x00` → le righe 30..165 tutte `0x00`.
- [ ] **Step 2:** FAIL. **Step 3:** implementare; `tools/screen_preview.c` accetta un secondo
  argomento opzionale `digest.bin` e scrive anche `today.pbm` e `today-alert.pbm`.
- [ ] **Step 4:** test host verdi, `idf.py build` senza warning; anteprima generata con un
  `digest.bin` scaricato dal backend locale (`curl -H "Authorization: Bearer <token del
  device di sviluppo>" http://localhost:8000/digest/today -o /tmp/digest.bin`, se il backend
  locale ha un'istantanea; altrimenti un file di prova a righe) e guardata.
- [ ] **Step 5: commit** `firmware: schermata Oggi composta con intestazione e piede`.

---

### Task 9: Firmware — download, SD e ciclo (`digest.c`, `app_main.c`)

**File:** crea `firmware/main/digest.{h,c}`; modifica `firmware/main/app_main.c`,
`firmware/main/display.{h,c}`, `firmware/main/CMakeLists.txt`.

**Interfacce prodotte:**

```c
typedef enum { DIGEST_NEW, DIGEST_SAME, DIGEST_NONE, DIGEST_FAILED } digest_fetch_t;
// GET SERVER_BASE_URL/digest/today con token e If-None-Match (se etag_in non vuoto).
// NEW: buf (SB_DIGEST_BYTES) e etag_out pieni; SAME: 304; NONE: 204; FAILED: rete, 4xx/5xx,
// dimensione sbagliata (buf non toccato in modo utile).
digest_fetch_t digest_fetch(const char *etag_in, uint8_t *buf, char *etag_out, size_t etag_len);
// SD: scrittura su file temporaneo e rename (mai un file a meta'), lettura con controllo
// della dimensione (digest_size_valid), cancellazione.
esp_err_t digest_save(const uint8_t *buf, const char *etag);
esp_err_t digest_load(uint8_t *buf, char *etag, size_t etag_len);   // ESP_ERR_NOT_FOUND / ESP_ERR_INVALID_SIZE
void      digest_clear(void);
// display.h
void display_today(const screen_status_t *st, const uint8_t *digest);
```

- `digest_fetch`: `http_client_config` + `http_client_set_auth` come `ota.c`
  (`http_get_to_buffer` è il modello: open, fetch_headers, status, read in loop fino alla
  lunghezza), header `If-None-Match`; l'ETag della risposta si legge con
  `esp_http_client_get_header` dopo `fetch_headers` (o con l'evento `HTTP_EVENT_ON_HEADER` se
  la versione di ESP-IDF non lo permette: verificare). Timeout `SB_DIGEST_TIMEOUT_MS`.
- `app_main.c`, dopo `do_sync` e prima della decisione di ridisegno:
  - buffer `SB_DIGEST_BYTES` con `malloc`; `etag_cached` da `digest_load` (se `sd_ok`);
  - se `st.wifi_ok` e batteria non sotto `SB_BATTERY_MIN_OTA_PCT`: `digest_fetch(etag_cached,
    ...)`; `NEW` → `digest_save` (se `sd_ok`) e usa il buffer nuovo; `SAME` → usa quello
    caricato; `NONE` → `digest_clear`, nessuna immagine; `FAILED` → quello caricato, se c'è;
  - `has_digest` + `etag_now` (o NULL);
  - `refresh = mode != BOOT_TIMER_SYNC || today_should_refresh(status_should_refresh(...),
    etag_now, retry_state_digest_shown())`;
  - se refresh: `show_status(&st, has_digest ? buf : NULL)` che sceglie `display_today` o
    `display_status`, poi `retry_state_set_shown(&view)` e
    `retry_state_set_digest_shown(etag_now)`;
  - `alert` = `!st.wifi_ok || st.auth_error || st.server_error || !st.sd_ok`;
  - log di una riga con l'esito (`digest: new/same/none/failed, etag ...`) per la verifica
    seriale; `free` del buffer.
- Niente cambia per DEV MODE, REC, messaggi brevi e per i cicli in cui il backend risponde
  204.

- [ ] **Step 1:** test host di quanto è puro (già in Task 7); qui la verifica è la build e
  l'hardware. `idf.py build` senza warning.
- [ ] **Step 2: verifica sull'hardware** (con l'autore, backend locale sul Mac con Home
  Assistant configurato, device puntato al Mac o al tunnel secondo `secrets.h` dello sviluppo;
  `version.txt` bumpato localmente e ripristinato dopo il flash): schermata "Oggi" dopo un
  click del tasto sopra; wake da timer senza novità → log `digest: same` e niente refresh;
  articolo aggiunto in Home Assistant → al wake successivo `digest: new` e schermata cambiata;
  senza rete → ultima immagine e piede "no wifi" in negativo; Home Assistant scollegato
  nelle Impostazioni → `digest: none` e schermata di stato classica.
- [ ] **Step 3: commit** `firmware: schermata Oggi scaricata dal backend` con nel corpo cosa è
  stato verificato sull'hardware.

---

### Task 10: Documentazione, prova dell'autore, release

**File:** `README.md` (schermata del device), `backend/README.md` (Home Assistant: token,
entità, endpoint, Pillow), `docs/stile-ui.md` (se la card introduce qualcosa), spec (Stato),
`CLAUDE.md` (stato, storico, "Fase 2" tolta dalle idee future).

- [ ] **Step 1:** documentazione; **commit** `docs: Home Assistant e schermata Oggi`.
- [ ] **Step 2:** backend locale (`docker compose up -d --build`), l'autore inserisce URL e
  token del suo Home Assistant e sceglie le entità; prova dal suo PC.
- [ ] **Step 3:** firmware: build finale, flash via cavo, pubblicazione della release per
  l'OTA sulla ZimaBoard **dopo** il deploy del backend.
- [ ] **Step 4:** merge, push e deploy sulla ZimaBoard **solo dopo l'ok dell'autore**; lì
  l'autore reinserisce URL e token (database diverso) e sceglie le entità.
