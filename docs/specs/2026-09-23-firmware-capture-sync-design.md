# Firmware Fase 1a — Cattura "record & forward" + sync — Design / Spec

**Data:** 2026-09-23
**Stato:** approvato in chat (design a sezioni), spec da eseguire tramite piano.
**Dipende da:** `docs/specs/2026-09-14-second-brain-design.md` (spec di progetto, §5),
`docs/specs/2026-09-16-firmware-bringup-ota-design.md` (Fase 0, completata e verificata
su hardware il 2026-09-23).

---

## 1. Obiettivo

Trasformare il firmware di bring-up (Fase 0: ciclo fisso di collaudo con registrazione
di 3 secondi) in un firmware **utilizzabile ogni giorno** per la funzione principale del
progetto: **cattura vocale rapida**, salvata in modo sicuro su microSD e **sincronizzata
in differita** verso il backend quando il device è sul Wi-Fi di casa.

Alla fine di questa fase il device, senza backend, deve:

1. registrare a durata libera tenendo premuto un tasto, con feedback su e-Paper;
2. non perdere mai una cattura (batteria scarica, riavvii, SD piena, rete assente);
3. svuotare la coda verso un server HTTP con un contratto stabile, che il backend
   (Fase 1b) implementerà tal quale;
4. avere l'ora giusta anche offline (RTC sincronizzato via NTP quando c'è rete);
5. dormire tra un'operazione e l'altra e restare aggiornabile via OTA come in Fase 0.

Fuori scope: trascrizione, vault, digest sul display (Fase 2), beep di conferma.

## 2. Vincoli hardware emersi in Fase 0

| Vincolo | Conseguenza sul design |
|---|---|
| Solo **BTN_PWR (GPIO18)** è RTC-capable e può svegliare dal deep sleep. **BTN_USER è GPIO0**, pin di strapping: non può essere wake source né essere tenuto premuto durante il reset. | La registrazione si comanda con **PWR tenuto premuto** (un solo gesto dal sonno). Il trigger DEV mode passa a USER, letto **dopo** il boot. |
| Refresh completo e-Paper ≈ 2 s, bloccante; refresh parziale non implementato. | La registrazione parte **prima** di disegnare "● REC"; il display mostra stati statici, niente contatore che scorre. |
| In deep sleep la USB-Serial/JTAG sparisce. | Flash e push solo in DEV mode (device sveglio). |
| Due AP con lo stesso SSID in casa, uno non inoltra TCP. | `WIFI_BSSID` opzionale in `secrets.h` (già fatto in Fase 0). |
| Il wake da deep sleep passa dal bootloader. | Conferma anti-rollback subito dopo l'init display, **prima** di ogni deep sleep (già fatto in Fase 0). |
| Velocità di upload osservata ≈ 140 KB/s. | Timeout HTTP di **inattività**, non totale; finestra di sync limitata. |

## 3. Decisioni (approvate in chat)

- **Approccio A**: macchina a stati **sequenziale**, un boot = un ciclo, poi deep sleep.
  Unica concorrenza: il task registratore (§5.2).
- **Coda = directory** `queue/` sulla SD, niente `queue.jsonl` (deviazione dalla spec di
  progetto §5, motivata: nessun indice che si corrompe a metà scrittura, stato sempre
  coerente col disco, riavvii gestiti gratis). L'id cattura è il nome file.
- **Trigger sync**: dopo ogni cattura, con budget; più **SYNC ONLY** su pressione breve.
- Nessun timer periodico di risveglio (YAGNI; il wake resta solo da tasto).
- Contratto upload: `POST /captures` con **corpo WAV grezzo** e metadati negli header
  (§6). Il server di test `tools/capture_server.py` ne è l'implementazione di riferimento.
- Durata massima cattura **10 min** (~19 MB); sotto **1 s** si scarta.
- Soglie batteria: **< 30 %** niente OTA; **< 10 %** niente registrazione e niente Wi-Fi.
- Scadenza di ciclo **5 min** (esclusa la registrazione) → deep sleep forzato.

## 4. Modalità di boot e flusso di un ciclo

Il device dorme in deep sleep (EXT1 su BTN_PWR). Al wake, `app_main` esegue in ordine:

```
boot base ──► scelta modalità ──► [CAPTURE] ──► SYNC ──► display stato ──► deep sleep
                    │
                    └──► [DEV] resta sveglio: Wi-Fi + server push/status
```

1. **Boot base**: log versione, `power_init`, `display_init`, `ota_mark_valid_if_pending`,
   avvio della **scadenza di ciclo** (esp_timer one-shot 5 min → `power_deep_sleep`).
   Ora di sistema caricata dall'RTC (`settimeofday`).
2. **Scelta modalità**: si campionano entrambi i tasti ogni 50 ms per **300 ms**
   (6 campioni, come oggi). Poi:

   | Condizione | Modalità |
   |---|---|
   | USER premuto in almeno un campione (con o senza PWR) | **DEV** |
   | PWR premuto in tutti i 6 campioni | **CAPTURE** (parte subito, nessuna altra attesa) |
   | PWR rilasciato prima della fine dei 300 ms | si continua a campionare **solo USER** fino a 1 s dal boot: se premuto → **DEV**, altrimenti **SYNC_ONLY** |
   | wake non da tasto (flash, reset, power-on) | **SYNC_ONLY** |

   Gesto DEV consigliato: sveglia con PWR e, con l'altra mano, USER già premuto o
   premuto subito dopo (entro 1 s).

3. **CAPTURE** (§5): monta SD → controlli (§5.4) → registra finché PWR è tenuto o 10 min
   → salva in coda → "Salvato m:ss".
4. **SYNC** (§6): se batteria ≥ 10 %: Wi-Fi (budget 8 s) → time sync → upload coda →
   OTA pull (se batteria ≥ 30 %). Se Wi-Fi assente: si salta tutto.
5. **Display di stato** (§7) e `power_deep_sleep()`.

**DEV**: niente cattura; Wi-Fi; `ota_dev_server_start()` (POST /ota) + `GET /status`;
resta sveglio (nessuna scadenza di ciclo). Trigger cambiato rispetto a Fase 0
(era PWR tenuto al boot).

Durata attesa di un ciclo senza registrazione ≈ 15 s (e-Paper 2×2 s, Wi-Fi ~3–4 s,
upload). La registrazione aggiunge la sua durata reale + ~2 s.

## 5. Cattura

### 5.1 Latenza

Ordine in CAPTURE: `storage_mount` → `audio_init` → **start registrazione** →
`display_text("● REC", …)`. Dalla pressione al primo campione ≈ 1 s (bootloader + boot
mode). Il primo secondo di parlato si perde: limite accettato del deep sleep.

### 5.2 Task registratore

`capture_start(path)` crea un task FreeRTOS (stack 4 KB, priorità sopra il main) che in
loop legge l'I2S a blocchi da **128 ms** (4096 byte a 16 kHz/16 bit mono) e li appende
al file aperto. Il main intanto disegna il display e campiona il tasto ogni 50 ms:
`capture_stop()` al **rilascio di PWR verificato su 2 campioni consecutivi**, oppure allo
scadere dei 10 min (controllato nel task stesso, così vale anche se il main è bloccato
nel refresh). `capture_stop()` attende la fine del task, fa `fsync`, patcha l'header WAV
con le dimensioni reali, chiude il file e ritorna la durata in ms.

Il buffer I2S è sull'heap (lezione della Fase 0: stack del main limitato). Un errore di
scrittura SD durante la registrazione ferma il task e viene riportato da
`capture_stop()`; il `.part` resta e viene gestito dal recupero al boot (§5.5).

### 5.3 Formato e nome file

WAV PCM, 16 kHz, mono, 16 bit, header 44 byte (`RIFF`/`WAVE`/`fmt `/`data`).

| Caso | Nome |
|---|---|
| RTC valido (anno ≥ 2024) | `cap_YYYYMMDD_HHMMSS.wav` (**UTC**, il server converte) |
| RTC mai sincronizzato | `cap_unsynced_NNNNNN.wav`, NNNNNN = contatore monotono in NVS (`sb/capseq`) |
| Collisione nello stesso secondo | suffisso `_2`, `_3`, … |

L'**id cattura** è il nome senza estensione. RTC, ora di sistema, nome file e
`X-Capture-Ts` sono in **UTC**. Il fuso serve solo per il display (§7): `TZ` fissa
`CET-1CEST,M3.5.0,M10.5.0/3` in `config.h` (`SB_TZ`), applicata con `setenv`/`tzset`.

### 5.4 Controlli prima di registrare

| Condizione | Display | Poi |
|---|---|---|
| batteria < 10 % | `Batteria scarica` | sleep (niente Wi-Fi) |
| SD non montabile | `SD assente` | SYNC (nulla da inviare, ma OTA/time sync sì) |
| spazio libero < 25 MB | `SD piena` | SYNC (può liberare spazio) |

### 5.5 Scrittura robusta e recupero

Si scrive su `queue/<nome>.wav.part`; a fine registrazione `fsync` → patch header →
`rename` a `.wav`. **Al boot**, prima di qualunque cosa sulla coda, `queue_recover()`
scandisce `queue/*.part`:

- dimensione ≥ 44 + 32000 byte (≥ 1 s di audio): patch header con la dimensione reale e
  rename a `.wav` (cattura interrotta da batteria/crash: si salva);
- altrimenti: cancellato.

Sotto 1 s anche una cattura normale viene scartata (rilascio accidentale) con display
`Scartato`. A 10 min il task si ferma da solo e il display mostra `Max 10:00`.

## 6. Coda e sync

### 6.1 Directory

```
/sdcard/queue/            catture in attesa (.wav) e in corso (.wav.part)
/sdcard/queue/rejected/   catture rifiutate dal server con 4xx (ispezionabili)
```

Ordine di invio: alfabetico sul nome (datate dalla più vecchia, poi le `unsynced`).

### 6.2 Contratto HTTP (implementato dal backend in Fase 1b)

```
POST {SECONDBRAIN_BASE_URL}/captures
Content-Type: audio/wav
Content-Length: <byte del file>
X-Capture-Id: cap_20260923_191530
X-Capture-Ts: 2026-09-23T19:15:30Z       (assente per le unsynced)
X-Device-Id: 70041dd8263c                (MAC STA, hex minuscolo)
X-Firmware-Version: 0.6.0
X-Battery-Pct: 95
X-Battery-Voltage: 4.15

<byte del WAV, in streaming dalla SD a blocchi di 4 KB>
```

Risposta attesa: JSON `{"id": "...", "status": "accepted" | "duplicate"}`. Il device
guarda solo il codice di stato:

| Codice | Azione del device |
|---|---|
| 200, 201 | cancella il file |
| 409 | duplicato già ricevuto: cancella il file (idempotenza sull'id) |
| altri 4xx | file non valido per il server: sposta in `rejected/`, prosegue |
| 5xx, timeout, errore rete | tiene il file, **interrompe il sync** per questo ciclo |

### 6.3 Budget

- Wi-Fi: `wifi_connect(8000)`. Fallito → `no wifi`, si salta tutto il resto del SYNC.
- HTTP: `timeout_ms = 15000` di **inattività** socket (un file da 19 MB può richiedere
  oltre un minuto a 140 KB/s).
- Finestra di sync: **3 min** dall'inizio dell'upload; alla scadenza non si iniziano
  nuovi file. Un file in corso si conclude o va in timeout.

### 6.4 Time sync

- Al boot: `settimeofday` dall'RTC PCF85063 (anche se non valido; il nome file lo
  gestisce §5.3).
- In SYNC con Wi-Fi: SNTP (`pool.ntp.org`, attesa max 5 s) se RTC anno < 2024 **oppure**
  ultima sincronizzazione (NVS `sb/lastntp`, epoch) più vecchia di 24 h. Su successo:
  `sensors_set_time(struct tm)` scrive l'RTC e aggiorna NVS.

### 6.5 Dopo il sync

`ota_pull(OTA_MANIFEST_URL)` solo se batteria ≥ 30 %. `OTA_MANIFEST_URL` resta separata
da `SECONDBRAIN_BASE_URL` finché il backend non serve anche il manifest.

## 7. Display di stato (fine ciclo)

Tre righe, refresh completo unico:

```
19:15                       (ora locale via SB_TZ; "--:--" se RTC non valido)
coda: 2  bat 95%
sync ok | no wifi | server ko | N inviate
```

È il display provvisorio finché la Fase 2 non porta `GET /digest.bmp`.
`display_text` cresce a tre righe (`display_text3` o `display_lines(const char *[], n)`).

## 8. DEV mode

- Trigger: **USER premuto entro 1 s** dal boot (§4). PWR tenuto al boot non è più DEV
  ma CAPTURE.
- Comportamento: Wi-Fi → `ota_dev_server_start()` (POST /ota come Fase 0) + nuovo
  `GET /status` → JSON:
  ```json
  {"version":"0.6.0","battery_pct":95,"battery_v":4.15,"time":"2026-09-23T19:15:30Z",
   "rtc_valid":true,"queue":["cap_20260923_191530.wav","cap_unsynced_000042.wav"],
   "queue_bytes":123456}
  ```
- Resta sveglio, nessuna scadenza di ciclo, display `DEV MODE` + IP.

## 9. Struttura del firmware (moduli nuovi/modificati)

```
firmware/main/
  app_main.c        flusso §4 (riscritto: niente ciclo di collaudo)
  power.c/.h        boot mode a 3 stati (DEV/CAPTURE/SYNC_ONLY), power_user_pressed(),
                    power_cycle_deadline_start(ms)
  capture.c/.h      task registratore §5.2: capture_start/capture_stop
  wav.c/.h          header WAV: wav_header_build(), wav_header_patch() (puro, host-test)
  queue.c/.h        directory queue/: queue_recover(), queue_list(), queue_next_name(),
                    queue_delete(), queue_reject(), queue_free_bytes()
  capture_name.c/.h nome file da struct tm / contatore (puro, host-test)
  sync.c/.h         upload §6: sync_run(budget_ms) → esito; sync_decide(status) (puro)
  timesync.c/.h     RTC↔sistema, SNTP, policy 24 h
  sensors.c/.h      + sensors_set_time(const struct tm*)
  display.c/.h      + display_lines(...) a 3 righe
  status_http.c/.h  GET /status (registrato sul server httpd di ota.c)
  audio.c/.h        audio_init resta; audio_record_wav (3 s fissi) rimosso, sostituito
                    da API a blocchi: audio_read_block(buf, len, timeout)
firmware/tools/capture_server.py   server di test POST /captures (§6.2), flag --fail-with
firmware/host_test/main/           + test_wav.c, test_capture_name.c, test_sync_decide.c,
                                     test_queue_recover.c (decisione su .part, pura)
```

Ogni modulo: una responsabilità, `esp_err_t` in uscita, nessun `ESP_ERROR_CHECK` nel
flusso normale (solo in `power_init`/`display_init`, dove senza non si va avanti).

## 10. Config & segreti

`main/secrets.h` (gitignored) aggiunge:

```c
#define SECONDBRAIN_BASE_URL "http://192.168.1.28:8000"   // server di test / backend
```

`secrets.h.example` aggiornato. `WIFI_BSSID` resta opzionale. Nuovo `main/config.h`
(committato) con le costanti di prodotto: `SB_TZ`, durata max, soglie batteria, budget
Wi-Fi/sync, spazio minimo SD. NVS namespace `sb`:
`capseq` (u32), `lastntp` (i64 epoch).

## 11. Testing

**Host (unity, target linux, `firmware/host_test`)** — funzioni pure:

- `wav_header_build/patch`: byte esatti per 16 kHz mono 16 bit, patch di `RIFF` e `data`.
- `capture_name_from_tm/unsynced`: `cap_20260923_191530`, `cap_unsynced_000042`, suffisso
  collisione.
- `sync_decide(status_code)`: 200/201/409 → DELETE, 4xx → REJECT, 5xx/errore → STOP.
- `queue_recover_decision(size)`: ≥ 44+32000 → PROMOTE, altrimenti DROP.
- policy time sync: `timesync_needed(rtc_year, last_ntp, now)`.

**Hardware-in-the-loop, per task** (seriale + `capture_server.py`), come in Fase 0.

**Scenario di accettazione finale (criterio di chiusura Fase 1a):**

1. Server spento. Tre catture con PWR tenuto (2 s, 10 s, 30 s): display `Salvato`,
   `no wifi` (o `server ko`), `coda: 3`. `GET /status` in DEV mode le elenca.
2. Server acceso. Pressione breve di PWR: `3 inviate`, `coda: 0`, i tre WAV sono nella
   cartella del server, riproducibili, con durata giusta.
3. Registrazione interrotta togliendo l'alimentazione (USB e batteria) dopo ~5 s: al
   boot successivo il `.part` è promosso e caricato al sync.
4. Pressione < 1 s: `Scartato`, nessun file.
5. SD estratta: `SD assente`, nessun crash, il ciclo termina in sleep.
6. Server che risponde 500 (`--fail-with 500`): il file resta in coda, `server ko`.
   Con 409: il file viene cancellato. Con 400: finisce in `rejected/`.
7. Ora: dopo il primo sync l'RTC è corretto; i nomi file successivi sono datati.
8. OTA pull ancora funzionante a fine ciclo (batteria ≥ 30 %); DEV mode con USER.

## 12. Non-goals (YAGNI, per ora)

- Beep di inizio/fine registrazione dall'altoparlante (feedback al buio): fase successiva.
- Refresh parziale e-Paper e contatore che scorre durante la registrazione.
- Digest dal server (Fase 2), provisioning Wi-Fi via BLE, HTTPS verso il backend
  (arriverà con il backend reale), compressione audio, VAD/auto-stop sul silenzio.
- Timer periodico di sync senza cattura.

## 13. Rischi

- **Bus condivisi**: SD (SDMMC) e e-Paper (SPI2) sono su bus diversi; audio (I2S) pure.
  Da verificare sul primo task HIL che il refresh dell'e-Paper durante la registrazione
  non causi perdita di campioni (controllo: durata del file ≈ tempo tenuto premuto).
- **Rilascio del tasto durante il refresh**: il main è bloccato ~2 s in `display_text`;
  il campionamento riprende dopo, quindi la registrazione può eccedere il rilascio fino
  a ~2 s nel caso peggiore (solo se il rilascio avviene durante il primo refresh).
  Accettato; se fastidioso, campionare il tasto dentro il task registratore.
- **NTP senza DNS**: se il router non fa DNS, `pool.ntp.org` fallisce: fallback all'IP
  del router come server NTP (molti router lo espongono), altrimenti si resta unsynced.
- **Consumo in tasca**: PWR premuto per sbaglio avvia registrazioni fino a 10 min. Il
  limite e lo scarto sotto 1 s contengono il danno; una protezione hardware è fuori scope.
