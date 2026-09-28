# secondbrain — backend

## Cos'è

Servizio che riceve le registrazioni dal device e-Paper (e in futuro da altri
dispositivi), le archivia su disco in `archive/AAAA/MM/GG/`, le rende consultabili e
gestibili da una UI web tipo "finder" e serve il firmware via OTA. Dettagli di design
in `../docs/specs/2026-09-28-backend-archivio-design.md`.

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
```

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
      091530_70041dd8263c.transcript.md   (futuro, AI) stesso nome base
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
errori del server (disco pieno `507`, DB non raggiungibile `503`): in questi casi il
device tiene il file e ritenta al ciclo successivo.

`GET /firmware/manifest.json` (alias di `GET /firmware/epaper154/manifest.json`) e
`GET /firmware/<tipo>/<file>.bin` servono l'OTA pull con lo stesso schema
`{version, url, sha256}` già implementato nel firmware.

`ALLOW_UNAUTHENTICATED_LAN=true` accetta richieste senza `Authorization` solo se il
client ha un IP privato o di loopback e la richiesta non porta l'header
`Cf-Connecting-Ip` (che Cloudflare aggiunge sempre alle richieste che passano dal
tunnel): dalla LAN di casa senza token quindi funziona, dal tunnel il token resta
sempre obbligatorio.

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

- **Database** (dispositivi, token, release firmware, utente, sessioni — configurazione,
  non archivio):

  ```bash
  docker compose exec -T postgres pg_dump -U secondbrain secondbrain > secondbrain.sql
  ```

**Ripristino su una macchina nuova:** copiare `archive/` e `firmware/` in `DATA_DIR`,
`docker compose up -d`, poi:

- con un dump: `docker compose exec -T postgres psql -U secondbrain secondbrain < secondbrain.sql`;
- senza dump: `docker compose exec app secondbrain rescan` ricostruisce le
  registrazioni dal disco; dispositivi e release firmware vanno registrati di nuovo
  (`secondbrain device add`, `secondbrain firmware publish`).

## Sviluppo

```bash
uv sync
docker compose -f docker-compose.test.yml up -d   # Postgres usa-e-getta per i test, porta 55432
uv run pytest -q
```
