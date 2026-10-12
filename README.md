# Second Brain

Dispositivo personale e-Paper per la **cattura vocale rapida**: un click, parli, un altro
click. La registrazione finisce su microSD e, appena il device trova una rete
Wi-Fi conosciuta, viene caricata su un backend di casa che la trascrive e la salva come
nota Markdown in un vault Obsidian. Nessun audio verso il cloud.

Il progetto ha due parti:

- `firmware/` — firmware ESP-IDF per la scheda **Waveshare ESP32-S3 e-Paper 1.54"** (B/N,
  200×200). Stato: **funzionante e verificato su hardware** (Fase 0 e Fase 1a complete).
- `backend/` — servizio `secondbrain` (FastAPI + archivio Postgres: ricezione,
  archiviazione su disco, UI finder, OTA). Stato: **Fase 1b in corso**; trascrizione e
  AI arriveranno dopo. Vedi `backend/README.md`.

Le decisioni di progetto sono in `docs/specs/`, i piani di lavoro in `docs/plans/`.

## Hardware

| Componente | Dettaglio |
|---|---|
| MCU | ESP32-S3-PICO-1-N8R8 (8 MB flash, 8 MB PSRAM), USB-Serial/JTAG nativa |
| Display | e-Paper 1.54" 200×200 B/N, controller SSD1681, su SPI2 |
| Audio | codec ES8311 (I2C + I2S), microfono analogico, 16 kHz mono 16 bit; altoparlante su header MX1.25, amplificatore su GPIO46 (attivo alto) |
| Storage | microSD via SDMMC 1-bit, FAT32 con nomi lunghi |
| Sensori | SHTC3 (temperatura/umidità), RTC PCF85063, tensione batteria (ADC) |
| Tasti | **PWR** (GPIO18, tasto sotto: registra) e **USER** (GPIO0, tasto sopra: sincronizza; pin di strapping); entrambi svegliano dal deep sleep |
| Batteria | LiPo 3,7 V su connettore **MX1.25 2 pin** (attenzione a polarità e tipo di connettore), caricabatterie ETA6098 a bordo. GPIO17 mantiene l'alimentazione da batteria (va tenuto alto, anche in deep sleep) |

La pin-map completa è in `firmware/main/board.h`. La scheda è la variante **B/N V2**
(`github.com/waveshareteam/ESP32-S3-ePaper-1.54`), non la "1.54G" a 4 colori.

## Come si usa il device

Il device dorme in deep sleep e mostra l'ultimo stato. Il tasto **sotto (PWR)** registra,
quello **sopra (USER)** sincronizza.

| Gesto | Cosa succede |
|---|---|
| **Click sotto** | **REGISTRA**. Un **beep** dopo ~1,7 s dal click dice "parla ora"; poi compare "* REC" con il nome del file. |
| **Click sotto** durante la registrazione | Stop: un **beep più lungo** conferma il salvataggio, compare "Salvato m:ss" e parte il SYNC. |
| Registrazione sotto 1 s | **Scartata** ("Scartato"), nessun beep finale. |
| Registrazione oltre 10 minuti | Si ferma da sola ("Max 10:00") e viene salvata. |
| **Tre click sotto** (anche due) entro 2 s dall'avvio | **DEV MODE**: la registrazione appena partita è scartata; Wi-Fi, server `POST /ota` e `GET /status`, resta sveglio. Il secondo click di un triplo click spesso cade mentre il device si avvia: per questo basta un click in più nei primi 2 s, e lo stop vale dopo. |
| **Click sopra** | **SYNC**: Wi-Fi, ora via NTP se serve, upload della coda, controllo OTA, schermata di stato, sleep. Circa 6–15 s. |
| **Click sopra** in DEV MODE | Riavvia in modalità normale (SYNC e sleep). |
| Tasto sopra durante una registrazione, tasto sotto durante un SYNC | Nessun effetto. |
| Alimentazione collegata / reset | SYNC. A batteria e a scheda spenta del tutto solo **PWR** la riaccende: va tenuto ~1 s, finché il firmware non aggancia l'alimentazione (GPIO17), poi registra. |

Attenzione: **USER (sopra) non va tenuto premuto mentre la scheda si accende da zero**
(USB inserita, reset). È il pin di boot del chip: basso in quell'istante porta in modalità
download USB. Dal deep sleep invece sveglia normalmente (verificato).

Schermata di stato a fine ciclo:

```
10:13                 77% [###]   ora locale; percentuale e icona della batteria
---------------------------------   (fulmine se in carica, "USB" senza batteria)
FASTWEB-45H7P7                     rete a cui si è collegato, oppure "nessuna rete"
coda: 0                            registrazioni in attesa
[ sync ok ]                        esito su fascia nera: "N inviate", "server ko", "Salvato 0:07", ...
```

La versione del firmware compare solo nella schermata DEV MODE. Anteprima delle schermate
sul Mac senza flash: `firmware/tools/screen_preview.c` (istruzioni in `CLAUDE.md`).

Se la SD manca o è piena il device lo dice ("SD assente", "SD piena") e non registra.
Sotto il 10 % di batteria non registra e non accende il Wi-Fi; sotto il 30 % non fa OTA
(solo a batteria). La scheda non segnala la presenza della USB né la carica: il firmware le
deduce da tensione, stabilità delle letture e collegamento dati USB, vedi `battery_policy.c`.
Se un ciclo si blocca per più di 5 minuti, il device si forza in deep sleep.

## Come funziona il firmware

Un risveglio = un ciclo lineare: boot → scelta modalità dai tasti → [registrazione] →
sync → schermata di stato → deep sleep. L'unica concorrenza è il task registratore, che
scrive l'audio dall'I2S alla SD mentre il task principale aggiorna il display.

- **Coda = directory** `queue/` sulla SD: un `.wav` è una cattura in attesa, l'ack del
  server lo cancella. Durante la registrazione il file è `.wav.part` con `fsync` ogni
  secondo: se manca l'alimentazione, al boot successivo viene recuperato (se ≥ 1 s) o
  cancellato. I rifiuti definitivi del server (`4xx` diversi da `401`/`403`/`429`)
  finiscono in `queue/rejected/`; un token rifiutato ferma il sync e lascia la coda com'è.
- **Nomi file** `cap_YYYYMMDD_HHMMSS.wav` in UTC dall'RTC; se l'RTC non è mai stato
  sincronizzato, `cap_unsynced_NNNNNN.wav` con contatore in NVS.
- **Wi-Fi**: scansione, poi connessione alla rete configurata presente con il segnale
  migliore. È possibile forzare il BSSID di un access point (utile se più AP condividono
  lo stesso SSID e uno non inoltra il traffico).
- **Ora**: l'ora di sistema viene caricata dall'RTC al boot; con la rete, SNTP aggiorna
  l'RTC se l'anno non è valido o l'ultima sincronizzazione ha più di 24 ore. Senza
  batteria l'RTC si azzera quando si toglie l'alimentazione.
- **OTA**: due partizioni app con rollback. *Pull*: a fine sync il device legge
  `<SERVER_BASE_URL>/firmware/manifest.json` (`{version, url, sha256}`) e se la versione è più
  nuova scarica, verifica lo sha256 e riavvia. *Push* (solo DEV): `POST /ota` con il
  binario nel corpo. Un'immagine appena installata si conferma dopo l'init del display;
  se crasha prima, il bootloader torna alla precedente.
- **HTTPS e token**: gli URL `https://` sono verificati col bundle di certificati di
  ESP-IDF; ogni richiesta al backend porta `Authorization: Bearer <DEVICE_TOKEN>` se il
  token è impostato.
- **Wake periodico**: il device si risveglia da solo ogni 5 min (anche a coda vuota) per
  inviare la coda, controllare l'OTA e aggiornare lo stato; a batteria sotto il 10 % si
  sveglia solo dai tasti. Dopo un wake da timer lo schermo si aggiorna solo se cambiano esito,
  coda, rete, stato di carica o la percentuale di almeno 2 punti.

Contratto di upload, che il backend dovrà implementare:

```
POST <server>/captures
Content-Type: audio/wav
X-Capture-Id: cap_20260923_191530
X-Capture-Ts: 2026-09-23T19:15:30Z        (assente per le unsynced)
X-Device-Id: 70041dd8263c                 (MAC)
X-Firmware-Version: 0.3.0
X-Battery-Pct: 95
X-Battery-Voltage: 4.15
X-Power-Source: battery                   (oppure usb; X-Battery-Pct solo a batteria)
<byte del WAV>
```

Risposte: `200`/`201` accettata e `409` duplicato → il device cancella il file;
`401`/`403` → il file resta, il sync si interrompe e il display dice `token ko`; `429`,
`5xx`, timeout o rete → il file resta e il sync si interrompe per quel ciclo; altri
`4xx` → file in `rejected/`.

## Sviluppo

### Prerequisiti

- ESP-IDF v5.3.x installato in `~/esp/esp-idf` (`. ~/esp/esp-idf/export.sh` prima di
  ogni `idf.py`).
- Python 3 per i server di test. `esptool` per il flash.

### Configurazione

Copia `firmware/main/secrets.h.example` in `firmware/main/secrets.h` (gitignored) e
compila la lista delle reti:

```c
#define WIFI_NETWORKS { \
    { "ssid-casa",    "password", "aa:bb:cc:dd:ee:ff" }, \
    { "ssid-ufficio", "password", NULL }, \
}
#define SERVER_BASE_URL "https://ingest.esempio.it"   // o http://192.168.1.28:8000 in LAN
#define DEVICE_TOKEN    "<token di secondbrain device add>"  // "" = senza token (solo LAN)
```

Il terzo campo di ogni rete è il BSSID da forzare, oppure `NULL`. Il server è uno solo
per tutte le reti (`SERVER_BASE_URL`, `http://` o `https://`); il token è quello stampato
da `secondbrain device add <mac>` sul backend (vedi `backend/README.md`).

Le costanti di comportamento (durata massima, soglie batteria, budget Wi-Fi, fuso
orario per il display, ...) sono tutte in `firmware/main/config.h`.

### Build, flash, monitor

```bash
cd firmware
idf.py build
idf.py -p /dev/cu.usbmodem1101 flash monitor    # device sveglio (DEV MODE) e USB dati collegata
```

In deep sleep la porta USB-Serial/JTAG sparisce: per flashare via cavo il device deve
essere in DEV MODE. In alternativa, senza cavo, dal Mac sulla stessa rete:

```bash
curl --data-binary @build/secondbrain_fw.bin http://<ip-device>/ota    # push (device in DEV MODE)
curl http://<ip-device>/status                                          # JSON: versione, batteria, ora, coda, tempi dell'ultimo ciclo
```

La versione è in `firmware/version.txt` (in git resta `0.1.0`; per i test OTA si bumpa
localmente e si ripristina).

### Server di test (al posto del backend)

```bash
cd firmware
python3 tools/capture_server.py [--port 8000] [--fail-with 500]
```

Riceve `POST /captures` (salva in `captures_inbox/`, `409` sui duplicati, `--fail-with`
forza un codice per provare i percorsi di errore) e serve `GET /firmware/*` da
`ota_serve/firmware/` per l'OTA pull. Per pubblicare un firmware da scaricare:

```bash
cd firmware
python3 tools/serve_firmware.py 0.2.0 192.168.1.28   # genera ota_serve/firmware/{manifest.json,secondbrain-0.2.0.bin}
```

(`serve_firmware.py` avvia anche un proprio server statico sulla stessa porta: usarne
uno solo alla volta, oppure generare il manifest e servirlo con `capture_server.py`).

### Test

La logica decisionale (header WAV, nomi file, esito HTTP → azione, recupero dei `.part`,
policy NTP, scelta della rete, parsing del manifest OTA) è in funzioni pure con test
unity eseguiti sull'host:

```bash
cd firmware/host_test
idf.py build && timeout 10 ./build/host_test.elf     # l'eseguibile non termina da solo
```

Il resto si verifica sull'hardware seguendo gli scenari di accettazione in
`docs/specs/2026-09-23-firmware-capture-sync-design.md` §11.

## Struttura del repository

```
docs/specs/     decisioni di design (progetto, Fase 0 bring-up+OTA, Fase 1a cattura+sync)
docs/plans/     piani di implementazione a task, con i comandi di verifica
firmware/       ESP-IDF: main/ (un modulo per responsabilità), host_test/ (unity su linux),
                tools/ (server di test), partitions.csv, sdkconfig.defaults
backend/        servizio secondbrain (archivio delle registrazioni, UI, OTA): vedi backend/README.md
```

Moduli principali in `firmware/main/`: `app_main.c` (flusso del ciclo), `power.c`
(tasti, boot mode, deep sleep, scadenza di ciclo), `capture.c` (task registratore),
`queue.c` (coda su SD), `sync.c` (upload), `wifi.c` + `wifi_select.c` (reti),
`timesync.c` (RTC/NTP), `ota.c` + `ota_manifest.c` (OTA pull/push/rollback),
`display.c` (e-Paper), `audio.c` (ES8311/I2S), `storage.c` (SD), `sensors.c`,
`board_i2c.c` (bus I2C condiviso), `status_http.c` (`GET /status`), `config.h`.

## Fasi

- **Fase 0** — bring-up hardware e OTA (pull, push, rollback): completata.
- **Fase 1a** — firmware "record & forward": cattura a tasto tenuto, coda su SD, sync,
  ora, display di stato, più reti Wi-Fi: completata.
- **Fase 1b** — backend `secondbrain`: `POST /captures` → Whisper → nota `.md` nel vault
  Obsidian + indice SQLite; serve anche il manifest OTA. Da fare.
- **Fase 2** — display "glanceable": `GET /digest.bmp` renderizzato dal server.
- **Fase 3** — arricchimento con agente (tag, action items), ricerca full-text.
- **Fase 4** — sync del vault con Syncthing.
