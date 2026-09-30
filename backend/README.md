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
