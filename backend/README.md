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

Il comando `device add` stampa il token una sola volta: va in `DEVICE_TOKEN` del
`secrets.h` del firmware (`device token <mac>` ne genera uno nuovo).
`ALLOW_UNAUTHENTICATED_LAN=true` in `.env` accetta upload senza token dalla sola LAN;
quando tutti i dispositivi mandano il token può tornare `false`.

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
      091530_70041dd8263c.wav             audio della registrazione (nelle note scritte: .txt col testo)
      091530_70041dd8263c.json            sidecar: metadati della registrazione
      091530_70041dd8263c.ai.json         risposte grezze dei provider AI, mai modificate
    .incoming/                            upload in corso (<uuid>.tmp), ripulita all'avvio
    .trash/2026/09/28/...                 cestino, con la stessa struttura dell'archivio (qui anche il .md)
  vault/                                  vault Obsidian (da condividere con Syncthing)
    Captures/2026/
      2026-09-28 09.15 Chiamare Marco.md  nota: trascrizione, titolo, riassunto, tag (frontmatter YAML)
  firmware/
    epaper154/
      secondbrain-0.6.2.bin
  postgres/                               dati del database (catalogo, non serve leggerli)
```

Il nome base di una registrazione è `HHMMSS_<device-id>` (ora locale di registrazione,
id del dispositivo), con suffisso `_2`, `_3`... in caso di collisione. Ogni file
"correlato" a una registrazione si chiama `<base>.<qualcosa>`; il sidecar è esattamente
`<base>.json`. La nota `.md` sta invece nel vault (vedi "Vault Obsidian"), legata alla
registrazione dall'`id` nel frontmatter. I file sono la fonte di verità: il catalogo
Postgres si ricostruisce da loro con `secondbrain rescan`.

## Comandi

Tutti come `docker compose exec app secondbrain ...`:

| Comando | Cosa fa |
|---|---|
| `device add <mac> --name <nome> [--type <tipo>]` | registra un dispositivo e genera il token (mostrato una volta sola) |
| `device token <mac>` | genera un nuovo token per un dispositivo (il precedente smette di valere) |
| `device list` | elenca i dispositivi registrati |
| `set-password [--stdin]` | imposta la password dell'interfaccia web |
| `unlock` | rimuove i tentativi di login falliti e sblocca l'accesso |
| `rescan` | ricostruisce il catalogo Postgres dall'archivio e dal vault su disco |
| `vault migrate` | sposta nel vault le note `.md` rimaste nell'archivio (una volta, dopo l'aggiornamento) |
| `firmware publish <file.bin> --version X.Y.Z [--type <tipo>]` | pubblica un binario come release corrente per l'OTA |
| `firmware rollback [--type <tipo>]` | torna alla release precedente |
| `firmware list` | elenca le release pubblicate (`*` = corrente) |
| `trash purge` | elimina definitivamente le registrazioni nel cestino scadute (oltre `TRASH_RETENTION_DAYS`) |
| `gen-key` | genera una `SETTINGS_KEY` (non serve il database) |
| `process <id>` | rimette in coda l'elaborazione AI di una registrazione (come "Rielabora") |
| `process --backfill` | mette in coda, a priorità bassa, tutte le note fuori dal cestino senza trascrizione |
| `retag --all` / `retag <id>` | rifà solo i tag (a priorità bassa) con le regole e il vocabolario attuali; trascrizione, titolo, riassunto e tag corretti a mano restano |
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
presente, in entrambi i casi non serve più); `401`/`403` → tiene il file, interrompe il
sync e mostra `token ko` (token da sistemare in `secrets.h`); `429`, `5xx`, timeout →
tiene il file e ritenta al ciclo successivo; qualunque altro `4xx` → sposta il file in
`rejected/` (non ritenta, il problema non si risolve da solo).

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
la trascrive, poi chiede titolo, riassunto breve e tag, e scrive la nota nel vault
(vedi sotto) e `<base>.ai.json` accanto al WAV. Ogni nota nuova entra in coda appena arriva.

**Tag e vocabolario dei nomi.** I tag contengono sempre le persone e i programmi, prodotti
e servizi nominati nella nota, più qualche tema; l'AI riusa i tag già presenti quando
calzano. Nella scheda **Vocabolario dei nomi** delle Impostazioni si elencano, uno per riga,
i nomi propri scritti come devono comparire (Costanza, Home Assistant, Claude…): vanno nel
`prompt` di Whisper (Groq, OpenAI) e nelle istruzioni di Gemini, così la trascrizione li
scrive giusti, e nei tag sempre nella stessa forma. Il pulsante "Rifai i tag delle note"
(o `secondbrain retag --all`) li applica alle note già archiviate.

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
coda, il finder dice perché: `SETTINGS_KEY` mancante o non valida, elaborazione in
pausa, nessun provider con una chiave o nessuno con un modello di testo. Il worker ritenta dopo 1 min, 5 min, 30 min, 2 h e 6 h; al
sesto tentativo fallito la nota resta "fallita".

**Correzioni a mano nel `.md`** (es. da Obsidian): le riprende il worker entro un minuto
(vedi "Vault Obsidian"); `secondbrain rescan` resta per la ricostruzione completa.

## Home Assistant

Il backend legge da Home Assistant il calendario di oggi, alcuni sensori e una lista della
spesa e ne compone la schermata "Oggi" del device (spec
`../docs/specs/2026-10-13-homeassistant-oggi-design.md`). Sola lettura: non scrive mai su
Home Assistant.

**Collegamento.** In Home Assistant: Profilo (in basso a sinistra) → Sicurezza → "Token di
accesso di lunga durata" → Crea token, e copiarlo subito (non lo mostra più). Nelle
**Impostazioni** del backend, card **Connessione Home Assistant** (serve `SETTINGS_KEY`):

- **URL** (`http://homeassistant.local:8123`, o l'indirizzo raggiungibile dal container) e
  **token**, cifrato come le chiavi API e mostrato solo mascherato. Cambiando l'URL il token
  va reinserito (altrimenti il vecchio token seguirebbe l'indirizzo nuovo); con lo stesso
  URL il campo vuoto lo lascia com'è.
- **Prova** controlla URL e token senza salvare e dice cosa non va (token rifiutato,
  timeout, certificato, "serve Home Assistant 2023.12 o successivo").
- **Carica entità da Home Assistant** elenca le entità: i **calendari** (caselle), fino a
  **6 sensori** (si scrive per cercare nei suggerimenti per nome o `entity_id`, accanto
  compare il nome di Home Assistant; l'**etichetta** breve è quella che finisce sul
  device) e la **lista della spesa** (una sola). Salvando le scelte l'immagine si
  aggiorna.
- Sotto: ultima lettura (con eventi, articoli e sensori trovati) o ultimo errore con
  l'ora, **Aggiorna ora**, l'anteprima dell'immagine e **Scollega** (cancella URL, token
  e scelte; il device torna alla schermata di stato).

**Aggiornamento.** Il worker legge Home Assistant ogni 5 minuti e subito dopo un salvataggio
o "Aggiorna ora". Se una lettura fallisce l'ultima istantanea resta quella buona e l'errore
compare nella card; i sensori più vecchi di 30 minuti portano "agg. HH:MM" nell'immagine.

**Endpoint.** `GET /digest/today` (token del device, solo hostname dei dispositivi o
quello unico) risponde `200` con l'immagine 1 bit 200×136 grezza (3.400 byte, MSB prima,
1 = nero) e un `ETag`; `304` se `If-None-Match` coincide; `204` se Home Assistant non è
configurato. L'anteprima della card è `GET /settings/homeassistant/preview.png` (con login).

**Immagine.** La disegna Pillow (`pillow` in `pyproject.toml`) col font Space Grotesk Bold
incluso in `secondbrain/assets/SpaceGrotesk-Bold.ttf`: il layout si cambia qui e al device
basta il sync, senza riflashare. Testo a 20 px; solo gli eventi di oggi (massimo 4: prima
quelli di tutto il giorno o iniziati prima, poi per ora), filetto, sensori uno per riga;
emoji e caratteri che il font non ha vengono tolti, i titoli lunghi finiscono con "…". La
lista della spesa è letta e salvata ma non compare nell'immagine.

**Se non funziona.** Gli errori della prova e della lettura sono scritti in italiano nella
card. "token rifiutato (401)": token sbagliato, scaduto o cancellato in Home Assistant.
"irraggiungibile" o timeout: dal container l'URL va raggiunto con l'indirizzo di rete (non
`localhost`); controllare porta e firewall. "non trovata" / "non trovato" accanto a una
scelta: l'entità salvata non esiste più in Home Assistant (rinominata o cancellata), va
scelta di nuovo. Il device non mostra "Oggi" finché `GET /digest/today` non risponde `200`
(con `204` resta la schermata di stato).

## Vault Obsidian

Le note delle registrazioni vivono in `DATA_DIR/vault/Captures/<anno>/` come
`<AAAA-MM-GG> <HH.MM> <titolo>.md`: una sola copia, scritta dal backend e leggibile e
modificabile con Obsidian (aprire `vault/` come vault). Per averle su telefono e portatile
si condivide `vault/` con Syncthing (configurazione esterna al backend; i file temporanei
`.syncthing.*` e i `*.sync-conflict-*` vengono ignorati e segnalati). L'audio resta
nell'archivio: il frontmatter porta `url`, il link alla registrazione nella UI, se
`PUBLIC_URL` è impostato nel `.env`.

Frontmatter: `id` (chiave: non toccarlo), `device`, `created`, `duration`, `title` e
`aliases`, `summary`, `tags`, `url`, poi i campi tecnici (`language`, `provider`, `models`,
`processed_at`, `edited`). Corpo: la trascrizione.

Cosa si può fare da Obsidian: modificare titolo, riassunto, tag e corpo; rinominare il
file (la parte dopo l'ora diventa il titolo). Il worker, quando è libero, ogni minuto
rilegge i file cambiati e applica le differenze come correzioni a mano (`edited`), che
"Rielabora" non sovrascrive; poi riscrive il file normalizzato e, se il titolo è cambiato,
lo rinomina. Lo stato dell'ultimo controllo è nella pagina Impostazioni. Note senza `id`,
con `id` sconosciuto o illeggibili vengono segnalate e lasciate stare: il vault può
contenere anche note tue.

Dalla UI: titolo, correzione della data, cestino e ripristino rinominano o spostano il
file (nel cestino la nota sta accanto al WAV in `archive/.trash/`, fuori dal vault); il
dettaglio elenca la nota tra i file e la serve da `GET /captures/<id>/note`.

Aggiornando da una versione in cui il `.md` stava accanto al WAV: `docker compose exec app
secondbrain vault migrate` sposta le note esistenti (una volta sola); `rescan` segnala
quelle ancora nell'archivio.

## Note scritte

Dall'app web, il pulsante **Nuova nota** nell'intestazione (sempre visibile) apre `/new`:
si scrive il testo (fino a 20.000 caratteri) e si salva. La nota nasce come una
registrazione già trascritta: dispositivo "App" (tipo `web`), nell'archivio il testo sta in
`<base>.txt` al posto del WAV, accanto al sidecar. Da lì segue la stessa strada delle
registrazioni: il worker fa solo l'arricchimento (titolo, riassunto, tag, con regole dei
nomi e vocabolario), poi archivio, "Da fare", ricerca, tag, vault, spunta e cestino. Il
testo scritto è la trascrizione e l'AI non lo cambia: "Rielabora" rifà solo titolo,
riassunto e tag. Testo vuoto o troppo lungo: errore 422 e il testo resta nel modulo; errore
del disco o imprevisto: 500, testo ancora lì.

Dopo il salvataggio si torna a "Da fare" (`/todo#salvata`). Il testo in corso è tenuto come
bozza nel browser (`localStorage`, chiave `sb-draft`) e si toglie solo a salvataggio
riuscito: una sessione scaduta o un errore non la perdono. La creazione è serializzata con
un advisory lock di Postgres, così un doppio invio non produce note doppie né file
cancellati. `secondbrain rescan` ricostruisce le note scritte dal `.txt` (anche senza `.md`),
senza chiamare provider di trascrizione.

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

**Con un `cloudflared` già presente sull'host** (come sulla ZimaBoard) non serve il profilo
`tunnel`: si aggiunge un public hostname verso la porta pubblicata dell'app. Se quel
`cloudflared` è un container sulla rete Docker di default, l'host si raggiunge come
`http://172.17.0.1:<APP_PORT>`. **Con un solo hostname** per UI e dispositivi lasciare
`DEVICE_HOSTNAME` vuoto: impostato, quell'hostname risponde solo a `/captures` e
`/firmware/*` e la UI darebbe 404. Gli upload dal tunnel chiedono comunque il token.

## Deploy in produzione

Sul server c'è un clone del repo; il `.env` sta in `backend/` accanto al compose e
`DATA_DIR` è un percorso assoluto. Dopo il push su GitHub:

```bash
ssh <server> '~/second_brain/backend/deploy.sh'
```

Lo script fa il backup del database in `$DATA_DIR/backup/`, aggiorna il codice con
`git pull --ff-only`, ricostruisce app e worker (le migrazioni le applica l'app all'avvio)
e stampa il commit a cui tornare in caso di problemi. Si ferma se il repo ha modifiche
locali o se `DATA_DIR` non è assoluto.

## Backup e ripristino

- **Archivio** (`archive/`): è la fonte di verità. Basta uno snapshot del NAS o:

  ```bash
  rsync -a "$DATA_DIR/archive/" <destinazione>/
  ```

- **Database** (dispositivi, token, release firmware, utente, sessioni, impostazioni e
  chiavi API cifrate — configurazione, non archivio; trascrizioni, riassunti e tag stanno
  anche nei `.md` del vault):

  ```bash
  docker compose exec -T postgres pg_dump -U secondbrain secondbrain > secondbrain.sql
  ```

- **`SETTINGS_KEY`** (nel `.env`): senza, le chiavi API del dump non si decifrano e vanno
  reinserite dalla UI.

**Ripristino su una macchina nuova:** copiare `archive/`, `vault/` e `firmware/` in `DATA_DIR`,
`docker compose up -d`, poi:

- con un dump: `docker compose exec -T postgres psql -U secondbrain secondbrain < secondbrain.sql`;
- senza dump: `docker compose exec app secondbrain rescan` ricostruisce le
  registrazioni dal disco, comprese trascrizioni, riassunti e tag dai `.md` del vault
  (ricollegati per `id`; le note senza `.md` tornano in coda); dispositivi, release firmware e impostazioni AI vanno
  registrati di nuovo (`secondbrain device add`, `secondbrain firmware publish`, pagina
  Impostazioni).

## Sviluppo

```bash
uv sync
docker compose -f docker-compose.test.yml up -d   # Postgres usa-e-getta per i test, porta 55432
uv run pytest -q
```

Le pagine web seguono le linee guida di stile in `../docs/stile-ui.md` (token, componenti,
dove va il colore d'accento, come si verifica una modifica con gli screenshot).
