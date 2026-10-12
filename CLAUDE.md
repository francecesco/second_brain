# Second Brain — istruzioni di lavoro e storico

Questo file è il contesto per chi (persona o assistente) riprende il progetto. Contiene
le regole, lo stato, lo storico e le idee future. Le decisioni di design dettagliate
stanno in `docs/specs/`, i piani eseguiti in `docs/plans/`, la guida d'uso in `README.md`.

## Regole

**Git e attribuzione**

- I commit sono firmati **solo** dall'autore umano: identità locale del repo
  `francecesco <francecesco78@gmail.com>` (account personale). Non usare l'identità
  aziendale (`advenias`) e non toccare la config git globale.
- **Nessun riferimento all'assistente** nei commit, nelle PR, nel codice, nei commenti,
  nella documentazione, nei nomi di file/branch/tag: niente `Co-Authored-By`, niente
  "Generated with". Questo file è l'unica eccezione, voluta dall'autore.
- Messaggi di commit in italiano, prefisso `firmware:`, `backend:` o `docs:`, corpo che
  spiega il perché e, se c'è, cosa è stato verificato sull'hardware.
- Il remote GitHub lo apre l'autore quando vuole: non creare repo né remote di iniziativa.
- Branch di lavoro per fase (`firmware-fase0`, `firmware-fase1a`, `backend-fase1b`);
  merge in `master` su decisione dell'autore. Il branch principale si chiama `master`.

**Codice firmware**

- `main/secrets.h` è gitignored e contiene le reti Wi-Fi con password: **mai committarlo**.
  `secrets.h.example` sì, con segnaposto.
- `version.txt` in git resta `0.1.0`; per i test OTA si bumpa localmente e si ripristina.
- Tutte le costanti di comportamento in `main/config.h`, niente numeri magici nei moduli.
- Un modulo per responsabilità, ritorno `esp_err_t`, nessun `ESP_ERROR_CHECK` nel flusso
  normale (solo dove senza non si va avanti: `power_init`, `display_init`).
- Buffer ≥ 1 KB sempre sull'heap: lo stack del task main è 10 KB e in Fase 0 gli array
  locali lo mandavano in overflow.
- La logica decisionale va in funzioni pure (`*_policy.c`, `capture_name.c`, `wav.c`,
  `wifi_select.c`, `ota_manifest.c`, `wifi_bssid.c`) con test unity sull'host in
  `host_test/`. Prima il test che fallisce, poi l'implementazione. Test host:
  `cd firmware/host_test && idf.py build && timeout 10 ./build/host_test.elf`
  (l'eseguibile non termina da solo). Al 2026-10-12 sono 43 e devono restare verdi.
- Build senza warning (`-Werror` su format-truncation è attivo).
- Ogni task del piano finisce con una verifica sull'hardware (log seriale o esito visibile)
  prima del commit; se la verifica scopre un bug, il fix va nello stesso commit o nel
  successivo con il perché nel messaggio.

**Documentazione**

- Nuova funzionalità architetturale → spec in `docs/specs/AAAA-MM-GG-<tema>-design.md`
  approvata a sezioni, poi piano a task in `docs/plans/`, poi esecuzione. Alla fine si
  aggiorna lo "Stato" della spec con data di verifica e deviazioni emerse.
- Le checkbox nei piani **non** vengono spuntate: lo stato si legge dai commit e dallo
  "Stato" della spec.
- La UI web del backend segue `docs/stile-ui.md` (stile "pannello": token, componenti,
  dove va l'arancione, verifica con screenshot). Una pagina o un componente nuovo si
  controlla contro quella guida prima del commit.

## Come si lavora con l'hardware

- Scheda: Waveshare ESP32-S3 e-Paper 1.54" **B/N V2** (SSD1681, SPI2). Non è la "1.54G"
  a 4 colori: usare il suo repo per driver/pin ha fatto fallire il primo bring-up.
- Porta seriale sul Mac: `/dev/cu.usbmodem1101`. **In deep sleep la porta sparisce**;
  per flashare via cavo il device deve essere in DEV MODE (PWR, rilascio, USER entro 1 s).
  Senza cavo dati (USB solo alimentazione) si lavora via Wi-Fi: `POST /ota` per il push,
  `GET /status` per lo stato, OTA pull pubblicando un manifest sul server di test.
- ESP-IDF v5.3.1 in `~/esp/esp-idf`: `. ~/esp/esp-idf/export.sh` prima di `idf.py`.
- Server: dal 2026-10-11 il backend di produzione è sulla **ZimaBoard** (`ssh zima`, utente
  `casaos`, IP Tailscale; in LAN `192.168.1.71`), in `~/secondbrain` con i dati in
  `/mnt/Storage1/secondbrain`, porta **8100** (la 8000 è di un'altra app, la 8080 di
  Stremio). Fuori: `https://secondbrain.cescohomelab.com`, un solo hostname per UI e
  dispositivi, dal tunnel `cloudflared` che c'era già sulla ZimaBoard (container sulla rete
  Docker di default, servizio verso `172.17.0.1:8100`). Il device usa quell'URL HTTPS da
  ogni rete. Deploy del codice: sulla ZimaBoard non c'è `rsync`, si usa `git archive
  --format=tar HEAD:backend | ssh zima 'tar -x -C ~/secondbrain'`, poi `docker compose up
  -d --build`. Il backend sul Mac (`cd backend && docker compose up -d`, porta 8000) resta
  per lo sviluppo; `tools/capture_server.py` come server di prova minimale.
- Rete di casa: due AP con lo stesso SSID FASTWEB; quello con BSSID `cc:2d:21:5f:59:29`
  non inoltra TCP (ping ok, connessioni in timeout in entrambe le direzioni). Per questo
  la voce di casa in `secrets.h` forza il BSSID del router `54:78:f0:bd:65:eb`.
- Rete dell'ufficio ("Advenias"): più AP, associazione in 1–2 s; con budget di 8 s la
  connessione a volte non arrivava, ora è 12 s dopo la scansione.
- Reti in `secrets.h` **in ordine di priorità**: casa, ufficio, hotspot "iPhone di
  Francesco" (dal 2026-10-11). Vince la prima in vista, non la più forte. L'hotspot si vede
  solo con "Massimizza compatibilità" attivo (altrimenti è a 5 GHz) e con la schermata
  Hotspot personale aperta o un dispositivo già collegato; IP `172.20.10.x`. Il backend in
  LAN da lì non si raggiunge: serve il tunnel. Su una rete sconosciuta l'hotspot è anche
  il modo per dare l'ora al device (NTP). iOS trasmette l'hotspot solo con la schermata
  aperta, con un client collegato o per ~90 s dopo: di fatto è un "sync a richiesta". Il
  Mac lo vede sempre perché usa l'hotspot istantaneo (Bluetooth/iCloud), non il beacon.
- Batteria collegata dal 2026-10-11 (1000 mAh, MX1.25). La scheda non dice se la USB è
  collegata né se sta caricando: lo stato si deduce dalla tensione (`battery_policy.c`). Con
  la batteria i campioni sono stabili (spread ~12 mV), senza erano sparsi su 2,9–4,18 V.
  Mantenimento a batteria (GPIO17) verificato il 2026-10-11: acceso al rilascio di PWR e
  di nuovo sveglio dopo il deep sleep, solo a batteria.
- Per catturare il log di un ciclo con il cavo: script Python che aspetta la comparsa di
  `/dev/cu.usbmodem1101` e legge, poi l'utente preme un tasto (o si aspetta il wake da 5 min).
- **Flash senza DEV MODE**: basta che il device sia sveglio (click di USER o wake del timer):
  appena compare la porta, `esptool` direttamente sui binari già compilati (da `build/`:
  `python -m esptool --chip esp32s3 -p /dev/cu.usbmodem1101 -b 460800 --before default_reset
  --after hard_reset write_flash @flash_args`, ritentando se la porta non è ancora pronta) lo
  mette in modalità download prima che torni a dormire. `idf.py flash` invece ricompila e
  perde l'attimo. "Porta stabile 8 s" non vuol dire DEV MODE: un ciclo col Wi-Fi dura ~11 s.
- **`version.txt` si rimette a 0.1.0 solo dopo il flash**: rimesso prima, `idf.py flash`
  ricompila una "0.1.0" e al primo ciclo l'OTA la riporta alla release pubblicata
  (successo il 2026-10-11).
- Trappola OTA: se sul server resta pubblicata una versione più alta di quella flashata
  (`secondbrain firmware list` sul backend, `ota_serve/firmware/manifest.json` con
  `capture_server.py`), il device si aggiorna a quel binario al primo ciclo. Dopo un flash
  via USB, `secondbrain firmware rollback` o pubblicare la versione flashata.
- OTA col backend: `docker compose cp <bin> app:/tmp/fw.bin` e `docker compose exec app
  secondbrain firmware publish /tmp/fw.bin --version X.Y.Z`; il manifest è generato
  dalla release corrente. Sulla ZimaBoard prima `scp <bin> zima:/tmp/` e i comandi dentro
  `ssh zima 'cd ~/secondbrain && ...'`.
- Audio: l'amplificatore dell'altoparlante (GPIO46) è **attivo alto** e impiega centinaia
  di ms a svegliarsi; il canale I2S TX va aperto solo per il beep (il full-duplex spostava
  il microfono sullo slot sbagliato) con `auto_clear` (altrimenti in underrun ripete
  l'ultimo blocco: beep in loop udibile in tutto l'ufficio, 2026-09-24). Buffer DMA del
  microfono da ~1 s, SD a 20 MHz: con meno si perdono campioni.
- **GPIO17 è il mantenimento dell'alimentazione da batteria** (BAT_Control nello schema
  Waveshare, `04_Hardware/Schematics` del loro repo), non l'abilitazione del partitore
  come credevamo in Fase 0: a batteria il tasto PWR accende la scheda solo finché è
  premuto e il firmware deve alzare GPIO17 subito e tenerlo (hold) anche in deep sleep.
  Portarlo a 0 spegne fisicamente. Con la USB non si nota. Il partitore di BAT_ADC è
  sempre collegato.
- La scheda **non ha un segnale di presenza USB**. Senza batteria il nodo VBAT legge valori
  casuali (2,9–4,18 V) per gli impulsi del caricabatterie; `battery_policy.c` deduce la
  sorgente da tensione minima, dispersione dei campioni, trend e link dati USB.
- Diagnostica senza seriale: `GET /status` → `last_cycle` con i tempi dell'ultimo ciclo
  reale (ms dall'avvio dell'app), picco audio, byte e tempo di upload, status HTTP, heap
  e stack liberi minimi (il 2026-10-07 in HTTP: heap 138 KB, stack del main 7,2 KB su 10).
  Il record sta in memoria RTC: si legge in DEV MODE solo se il ciclo è stato fatto prima
  di entrarci e senza togliere l'alimentazione.
- Il refresh completo dell'e-Paper "lampeggia" in nero per ~1,5 s: è il pannello, non un
  bug. Il refresh parziale non è implementato. Dal 2026-10-11 si usa la forma d'onda di
  fabbrica (OTP, `0x22=0xF7`): la LUT custom dell'esempio Waveshare dava neri grigi.
- Schermate disegnate in `screen.c` (C senza ESP-IDF) con Space Grotesk Bold in bitmap
  (`font_grotesk.c`, generato da `firmware/tools/gen_font.py` dal woff2 della UI web).
  Anteprima sul Mac senza flash: `cc -I main tools/screen_preview.c main/screen.c
  main/font_grotesk.c -o /tmp/preview && /tmp/preview <dir>` scrive un PBM per caso.
- Tasti (dal firmware 0.8.3, spec `docs/specs/2026-10-11-firmware-tasti-click-design.md`):
  **sotto = PWR** (click registra, click ferma; un click in più nei primi 2 s = DEV MODE),
  **sopra = USER** (click sincronizza; in DEV MODE riavvia). GPIO0 sveglia dal deep sleep
  senza modalità download (verificato), ma non va premuto durante un'accensione da zero.
- Per riavviare un device fermo in DEV MODE: click del tasto sopra (USER, dal firmware 0.8.3;
  prima era PWR), oppure push di un firmware, oppure `esptool ... --after hard_reset chip_id`
  con il cavo dati.

## Stato al 2026-10-11

- Tutto su `master`, compreso il backend (`backend-fase1b` e `backend-elaborazione-ai` uniti).
  `origin` (`github.com/francecesco/second_brain`, pubblico) allineato a `master` il
  2026-10-11 (push su richiesta dell'autore).
- **Hardware e firmware: chiusi**, batteria collegata. Firmware sul device: 0.8.3 (build
  del codice corrente di `master` coi tasti a click, flashata via cavo e pubblicata come
  release corrente sulla ZimaBoard il 2026-10-12; `version.txt` in git resta 0.1.0), con
  `SERVER_BASE_URL "https://secondbrain.cescohomelab.com"` e il token generato sulla
  ZimaBoard. Dal 2026-10-07 il firmware parla HTTPS (bundle di certificati) e manda il token
  (spec `docs/specs/2026-10-07-firmware-https-token-design.md`): un solo URL per tutte le
  reti, 401/403 non svuotano più la coda. Dal 2026-10-11 il **wake periodico** (spec
  `docs/specs/2026-10-11-firmware-wake-periodico-design.md`, sostituisce il backoff del timer
  di sync del 2026-10-07): il device si sveglia da solo ogni 5 min, con o senza coda, tranne
  a batteria sotto il 10 %; lo schermo si aggiorna ai wake da timer solo se cambiano esito,
  coda, rete, stato di carica o la percentuale di 2 punti. Fuso in `master` il
  2026-10-11. Intervallo in `config.h`, da rivedere con le misure di consumo a batteria.
- Batteria: collegata il 2026-10-11. Display `bat NN%`, `in carica NN%`, `bat piena`
  (da 4,17 V su USB) o `USB` senza batteria; `/status` con `battery_present` e `charge`.
  La percentuale usa ancora la curva LiPo tipica e in carica è sovrastimata: la taratura
  con una scarica completa l'autore l'ha rimandata. La **tensione** invece è corretta del
  2,7 % (`SB_BATTERY_MV_SCALE_PERMILLE`): l'ADC leggeva 4091 mV a fine carica, cioè i
  4,20 V del caricabatterie (stima senza multimetro, da rifare con una misura vera). Mantenimento a batteria verificato.
- **Backend Fase 1b** (`backend/`, spec `docs/specs/2026-09-28-backend-archivio-design.md`):
  **in produzione sulla ZimaBoard dal 2026-10-11**, partito da zero (database nuovo,
  `SETTINGS_KEY` nuova generata lì, nuovo token del device in `secrets.h`),
  `ALLOW_UNAUTHENTICATED_LAN=false`, `PUBLIC_URL=https://secondbrain.cescohomelab.com`,
  `DEVICE_HOSTNAME` vuoto (con un solo hostname la UI darebbe 404). Release firmware
  0.8.3 pubblicata lì. Chiavi API dei provider AI reinserite dall'autore il 2026-10-12: ciclo
  completo verificato (upload dal device, trascrizione con Groq, nota con titolo nel vault
  `/mnt/Storage1/secondbrain/vault/Captures/`). Sul Mac resta il backend di sviluppo
  (`backend/.env` e `backend/data/` locali e gitignored).
- **Elaborazione AI** (spec `docs/specs/2026-09-29-backend-elaborazione-ai-design.md`):
  trascrizione, titolo, riassunto e tag con Groq e Gemini (chiavi inserite dall'autore
  nella pagina Impostazioni, cifrate con `SETTINGS_KEY` del `backend/.env` del Mac), worker
  separato nel compose, ricerca full-text. Verificata sul Mac il 2026-09-30 e, per gli
  scenari rimandati, il 2026-10-07 (vedi "Stato" della spec). Unita in `master` il
  2026-09-30.
- **Note nel vault Obsidian** (spec `docs/specs/2026-10-07-backend-vault-obsidian-design.md`):
  dal 2026-10-07 il `.md` di ogni registrazione sta in `data/vault/Captures/<anno>/<data>
  <ora> <titolo>.md` (una sola copia, chiave `id` nel frontmatter), il worker riprende ogni
  minuto le modifiche fatte in Obsidian, `secondbrain vault migrate` ha spostato le note
  esistenti sul Mac. Da provare con Obsidian vero (installazione dell'8 ottobre) e con
  Syncthing sulla ZimaBoard.
- Le catture di prova di `capture_server.py` sono state cancellate il 2026-09-29.

## Prossima sessione

0. **Deploy sulla ZimaBoard della vista "Da fare" / "Completati" / menu** (in `master`,
   provata dall'autore sul Mac; sulla ZimaBoard c'è la prima versione): quando l'autore
   dà l'ok, `git archive --format=tar HEAD:backend | ssh zima 'tar -x -C ~/secondbrain'`
   e `docker compose up -d --build`.
1. **Chiudere il deploy** (Task 14 del piano `docs/plans/2026-09-28-backend-archivio.md`):
   dal telefono su rete mobile login e blocco dopo 5 tentativi; `sudo reboot` della ZimaBoard con servizi e tunnel che tornano da soli;
   backup (`pg_dump` del README e snapshot di `/mnt/Storage1/secondbrain`, compresa la
   `SETTINGS_KEY` del `.env`).
2. **Vault con Syncthing** sulla ZimaBoard: `/mnt/Storage1/secondbrain/vault` condiviso col
   Mac, prova con Obsidian vero.
3. Tasti: resta da provare l'accensione a batteria da scheda spenta del tutto (PWR tenuto
   ~1 s).
4. Batteria: scarica completa per la curva della percentuale e misura del consumo per
   decidere se il wake ogni 5 min regge.

## Storico

**2026-09-14 — Progetto.** Spec di progetto: device "record & forward", vault Obsidian
come fonte di verità, SQLite/FTS5 solo indice, STT locale con faster-whisper, backend
FastAPI su ZimaBoard, PiAgent solo on-demand, Syncthing per il vault. Piano TDD del
backend in 10 task (`docs/plans/2026-09-14-secondbrain-backend.md`, non ancora eseguito).

**2026-09-16/17 — Fase 0, bring-up + OTA** (`docs/specs/2026-09-16-firmware-bringup-ota-design.md`).
Hardware verificato, ESP-IDF installato, pin-map, partizioni OTA, e-Paper (dopo aver
scoperto la variante B/N), tasti con dev mode su PWR (GPIO0 è strapping), microSD via
SDMMC, audio ES8311 → WAV, sensori, deep sleep con wake su PWR, Wi-Fi, parsing manifest
con test host, OTA pull. Tre round di debug sul pull: buffer sull'heap (stack overflow),
NUL-termination del manifest, lettura completa del body HTTP.

**2026-09-23 — Chiusura Fase 0.** Pull verificato end-to-end (0.1.0 → 0.2.0); il primo
fallimento era l'AP di casa che non inoltra TCP → `WIFI_BSSID` forzato. OTA push
`POST /ota` in DEV mode (1.1 MB in ~8 s). Rollback confirm dopo l'init display: senza,
ogni immagine OTA veniva annullata al primo risveglio (il wake passa dal bootloader);
verificato con un firmware volutamente rotto. Merge di `firmware-fase0` in `master`.

**2026-09-23 — Fase 1a, cattura + sync** (`docs/specs/2026-09-23-firmware-capture-sync-design.md`,
piano in 11 task). Decisioni: PWR tenuto = registra (unico tasto che sveglia); coda =
directory `queue/` senza file indice; sync dopo ogni cattura con budget; contratto
`POST /captures` con WAV grezzo e metadati negli header; durata max 10 min, scarto sotto
1 s; soglie batteria 10 %/30 %; scadenza di ciclo 5 min. Emersi sull'hardware: FAT senza
nomi lunghi (→ `CONFIG_FATFS_LFN_HEAP`); init I2C non tollerante all'ordine audio/sensori
(→ `board_i2c_ensure`, che accende anche il ramo di alimentazione periferiche, senza cui
l'RTC andava in timeout); `.part` da 0 byte dopo taglio di alimentazione (→ `fsync` ogni
secondo); latenza pressione→registrazione ridotta di 1,2 s spostando l'init display dopo
l'avvio della registrazione (ora ~1,9 s). Tutti gli scenari di accettazione passati:
cattura, scarto, recupero `.part`, upload con ack, server 500, SD assente, NTP → RTC,
OTA pull + conferma, DEV con USER, `GET /status`.

**2026-09-24 — Più reti e display.** `WIFI_NETWORKS` in `secrets.h` (ssid, password,
bssid opzionale, server per rete), scansione e scelta della rete presente col segnale
migliore (`wifi_select`, test host), budget Wi-Fi 12 s dopo la scansione, retry sulla
prima connessione HTTP dopo l'associazione, log dei reason code. Verificato dall'ufficio
con upload e OTA pull dal Mac in ufficio. Display di stato a quattro righe (ora +
versione, rete, coda + batteria, esito). PWR in DEV MODE riavvia in NORMAL.
README e questo file.

**2026-09-24 pomeriggio — Risposta dei tasti, beep, audio, SD.** Misurati sul device:
beep "parla ora" a ~1,7 s dalla pressione, stop entro ~0,2 s dal rilascio (rilevato dal
task registratore), beep di fine e "Salvato" subito invece che a fine sync (~10 s prima).
Trovato e risolto un difetto serio: le registrazioni perdevano fino al 23 % dell'audio
(buffer DMA da 90 ms + SD a 400 kHz); ora 18,625 s su 18,627. SD a 20 MHz: upload da ~35
a ~210 KB/s. Scelto un solo beep finale lungo (uno dei due corti non si sentiva).
Batteria: serve connettore MX1.25 2 pin, controllando la polarità.

**2026-09-28 — Collaudo completo e basi della batteria.** Collaudo hardware completo
superato (OTA pull/push, catture fino a 55 s senza perdite, scarto, taglio di corrente,
SD assente, server spento, DEV + uscita con PWR); tolto un clic sul primo campione.
Dallo schema elettrico: GPIO17 è il mantenimento dell'alimentazione da batteria (il
firmware lo abbassava: a batteria il device si sarebbe spento al rilascio di PWR),
corretto con hold in deep sleep. Riconoscimento USB/batteria per stima (nessun segnale
hardware), percentuale da curva LiPo tipica, soglie solo a batteria, display "USB" o
"bat NN%", header `X-Power-Source`. Da tarare con la batteria reale.

**2026-09-29/30 — Elaborazione AI delle note** (`docs/specs/2026-09-29-backend-elaborazione-ai-design.md`,
piano in 15 task). La ZimaBoard (Celeron N3450 senza AVX, RAM condivisa con Immich) non
regge faster-whisper: si usano provider esterni intercambiabili (Groq, Gemini, poi OpenAI)
con un principale e riserve, chiavi cifrate impostate dalla UI. Worker separato con coda in
Postgres (`SKIP LOCKED`, lease, priorità, backoff 1 min → 6 h), due fasi (trascrizione,
arricchimento), file `<base>.md` (verità dei campi AI, frontmatter leggibile da Obsidian) e
`<base>.ai.json` nella cartella del giorno, correzioni a mano protette da "Rielabora",
ricerca full-text in italiano. Emersi in revisione: lock di riga su tutte le operazioni
della nota con ordine nota → lavoro, rescan che poteva svuotare una trascrizione se
girava insieme al worker, chiavi finte nei test che somigliavano a chiavi vere (repo
pubblico), tag nella lista attaccati al titolo. Verificato col device: Groq, Gemini,
arretrato.

**2026-09-28/29 — Fase 1b, backend archivio** (`docs/specs/2026-09-28-backend-archivio-design.md`,
piano in 14 task). Decisioni: niente AI né Obsidian in questa fase; FastAPI + Postgres in
Docker; i file sono la verità (`archive/AAAA/MM/GG/HHMMSS_<device>.wav` + sidecar JSON) e
Postgres è un catalogo ricostruibile con `rescan`; UI finder con login singolo, titolo,
cestino recuperabile, correzione data, filtro per dispositivo; accesso da fuori con
tunnel Cloudflare; token per dispositivo, upload senza token solo dalla LAN; OTA servito
dal backend. Emersi in revisione: blocco del login per client (un blocco globale era un
DoS), login serializzato, bypass dell'hostname dei dispositivi col punto finale, rescan
che cancellava righe con sidecar rotto, pubblicazione IPv6 che allargava la regola LAN.
Su Docker Desktop l'app vede l'IP del gateway, non quello del device. Verificato col
device vero: upload, finder, OTA 0.6.2 → 0.6.3. Poi leggibilità: nomi dei giorni, titolo
di default per fascia del giorno, icone.

**2026-10-07 — Test end-to-end AI e firmware HTTPS + token.** Verificati sul Mac i sette
scenari rimandati dell'elaborazione AI (fallback con chiave sbagliata, SIGTERM e SIGKILL
del worker con lease, pausa, correzione + Rielabora, cestino durante l'elaborazione,
rescan dopo modifica a mano; ricerca a 390 px con Chrome headless). Emerso: Gemini a
volte risponde con un candidato senza parti di testo (`content: {}`, STOP, quasi 2000
token di "thoughts") che veniva letto come silenzio; ora è un errore del servizio con
fallback. Poi la spec firmware "HTTPS + token" (`docs/specs/2026-10-07-firmware-https-token-design.md`):
un solo `SERVER_BASE_URL`, `DEVICE_TOKEN` in `secrets.h`, modulo `http_client` con bundle
di certificati e Bearer, 401/403 → `token ko` senza toccare la coda, 429 come i 5xx,
`last_http` e `free_heap_min` in `/status`. Verificato con un quick tunnel di Cloudflare
(`cloudflared tunnel --url`, serve ~1 min di propagazione): 401 dal tunnel con coda intatta,
upload e OTA pull in HTTPS col token, LAN in HTTP invariata; heap libero minimo con TLS
124 KB (niente PSRAM). Il device si trova in LAN con `curl http://192.168.0.x/status` su
tutto il /24 (DEV MODE).

**2026-10-07 — Vista per tag e note nel vault Obsidian.** Pagina `/tags` con le tessere dei tag.
Poi la spec del vault: una sola copia della nota in `vault/Captures/`, nome con data, ora e
titolo (rinominato dal backend quando il titolo cambia), frontmatter con `id`, `aliases`,
`url`; `vault_sync` nel worker (mtime, ogni minuto) che applica le modifiche come correzioni
a mano; cestino che porta la nota accanto al WAV; `vault migrate` e `rescan` per `id`.
Emerso in esecuzione: il titolo di default non va nel frontmatter; un file rinominato vince
sul `title`; i test vecchi tengono la nota accanto al WAV passando `vault=None`.

**2026-10-07 — Timer di sync.** Wake dal timer di deep sleep armato solo con coda non vuota
(15 min, raddoppio fino a 4 h tra wake da timer consecutivi, azzerato dal tasto), modalità
`BOOT_TIMER_SYNC` senza campionamento dei tasti, esito mostrato tenuto in memoria RTC e
refresh dell'e-Paper solo se cambia qualcosa, `retry_attempt` e `next_wake_s` in `/status`.
Verificato: wake automatico a +15 min con upload e un solo refresh, nessun wake a coda vuota,
primo raddoppio osservato. Lezioni: `docker compose up --build` per un altro branch riaccende
l'app e rovina una prova con "server fermo"; il DHCP dell'ufficio cambia gli IP di Mac e
device (il device si ritrova scansionando `/status` sul /24).

**2026-10-11 — Batteria e hotspot.** Collegata la batteria (1000 mAh). Su USB il display
mostrava solo "USB": ora la batteria si riconosce dai campioni stabili e la percentuale si
mostra anche in carica, con lo stato dedotto dalla tensione (nessun segnale dal
caricabatterie). Verificato: "in carica 83%" con la tensione salita 3977 → 4058 mV in
pochi minuti. L'autore ha fatto notare che l'80 % letto in carica con la curva generica
non vale: la taratura vera richiede una scarica completa, rimandata. Poi le reti in
ordine di priorità (prima vinceva il segnale più forte: l'hotspot in tasca avrebbe battuto
casa) con l'hotspot dell'iPhone come ultima scelta; verificato da una rete sconosciuta:
al primo ciclo l'hotspot non era ancora visibile, al secondo collegato in ~1,2 s, NTP ok,
OTA verso il Mac in LAN in timeout (~21 s di Wi-Fi sprecati per ciclo finché non c'è il
tunnel). Mantenimento a batteria verificato (77 % a batteria dopo il distacco). Poi il
**wake periodico** ogni 5 min al posto del timer di sync con backoff: lo schermo staccando
la USB non cambiava finché non si premeva PWR. Verificato su USB (wake a +298 e +601 s,
ciclo di ~3,8 s senza reti, schermo fermo) e staccando la USB (passa da solo a "bat NN%").
Emerso che l'hotspot dell'iPhone di solito non trasmette: il Mac lo vede tramite l'hotspot
istantaneo.

**2026-10-11 — Display.** Neri più intensi (forma d'onda di fabbrica al posto della LUT
custom, refresh da 1,8 a 1,4 s; font Space Grotesk Bold al posto del Courier da 1 px),
ora in alto a sinistra, icona della batteria in alto a destra (fulmine in carica, "USB"
senza batteria), esito in negativo su fascia nera, versione solo in DEV MODE. Layout
deciso con anteprime renderizzate sul Mac, poi verificato sul device (0.8.0). Poi la
batteria ferma all'83-86 % da ore su USB: era carica, ma l'ADC leggeva ~110 mV in meno
(partitore e tolleranza). Fattore ×1,027 ricavato dal massimo in carica, soglia USB a
4,23 V e "piena" a 4,15 V; verificato: "bat piena" su USB e 96 % appena staccata.

**2026-10-12 — Note da fare e menu.** Ogni registrazione si spunta come fatta: esce
dall'archivio e da "Da fare" e passa in "Completati" (spec
`docs/specs/2026-10-12-backend-note-da-fare-design.md`); stato nel sidecar (`done_at`), nel
catalogo (migrazione 0006) e come `completata` nel frontmatter del vault, mai riletto. Il
titolo di una riga apre e richiude il dettaglio; le voci dell'intestazione stanno in un
menu a scomparsa, senza "Ricerca". Lezione: la prima versione l'ho mandata subito sulla
ZimaBoard; l'autore vuole provare prima in locale sul Mac (`docker compose up -d --build`
in `backend/`, http://localhost:8000) e decide lui il deploy. Sulla ZimaBoard gira ancora
quella prima versione (casella con titolo barrato, senza "Completati").

**2026-10-12 — Tasti a click.** Tenere premuto per registrare era scomodo: ora il tasto
sotto (PWR) registra e ferma a click, tre click portano in DEV MODE, il tasto sopra (USER,
GPIO0) sveglia e sincronizza ed esce dalla DEV MODE. Prima verificato che GPIO0 come
sorgente di wake non porta il chip in modalità download. Emerso: il secondo click di un
triplo click cade durante l'avvio (~0,5 s) e non si vede, quindi conta come DEV qualunque
click in più nei primi 2 s. I click li conta un task che campiona PWR ogni 10 ms (il main
resta bloccato nel refresh dell'e-Paper). Verificato su USB: registrazione di 8 s senza
perdite con upload dall'ufficio, triplo click con scarto e DEV, uscita con USER, sync con
USER; poi a batteria tutti i gesti, con l'upload arrivato sulla ZimaBoard. Release 0.8.3
pubblicata per l'OTA. Flash senza DEV MODE con `esptool` appena il device si sveglia.

**2026-10-11 — Deploy sulla ZimaBoard.** Backend in Docker sulla ZimaBoard, partito da
zero, dati su `/mnt/Storage1/secondbrain`, porta 8100 (8000 e 8080 occupate da altre
app), esposto da `https://secondbrain.cescohomelab.com` col tunnel `cloudflared` che
c'era già. Emersi: niente `rsync` sulla ZimaBoard (si usa `git archive`); con un solo
hostname `DEVICE_HOSTNAME` va lasciato vuoto; lo User-Agent dell'ESP32 passa (`401`
senza token, niente Bot Fight Mode). Firmware 0.8.1 con l'URL HTTPS del tunnel inviato
via `POST /ota`: al primo ciclo dal Wi-Fi di casa ha caricato le due registrazioni in
coda (`201`) e chiesto il manifest; poi release 0.8.1 pubblicata sulla ZimaBoard.

**2026-10-06 — Stile della UI web.** Scelta tra tre direzioni con mockup (pannello alla
Teenage Engineering, dot matrix alla Nothing, 1-bit come il device): vinta la prima,
dose moderata di arancione. CSS scritto a mano su variabili, font squadrati vendorizzati (Space Grotesk, Space Mono, Doto),
LED di stato, voce di menu attiva. Linee guida in `docs/stile-ui.md`. Branch
`backend-stile-pannello`, fuso in `master` il 2026-10-07.

## Idee future e cose rimandate

- **Backend dopo l'elaborazione AI**: adattatore locale (faster-whisper o altro) passando a
  un NAS più potente; divisione in pezzi dell'audio oltre i limiti dei provider (serve solo
  alzando la durata massima); Daily notes e arricchimento con il PiAgent nel vault.
- **Fase 2 — display glanceable**: `GET /digest.bmp` 200×200 1-bit renderizzato dal
  server, cache su SD, mostrato al posto della schermata di stato.
- **Refresh parziale e-Paper**: contatore che scorre durante la registrazione, meno
  lampeggi.
- **Batteria** (collegata, 1000 mAh, mantenimento verificato): consumo in deep sleep e per
  ciclo, autonomia col wake ogni 5 min (stima ~1 settimana, ~4 giorni col backend
  irraggiungibile); se non basta, intervallo diverso a batteria o backoff sui fallimenti. **Taratura** proposta e rimandata: carica piena registrando la curva
  di carica, poi scarica completa a device sveglio con log mV/tempo su SD ogni 30 s fino allo
  spegnimento, CSV letto dalla seriale in DEV MODE; percentuale = frazione di autonomia
  rimasta, 0 % con margine sopra la tensione di spegnimento; curva da sostituire in
  `battery_policy.c`, più lo scarto in mV dovuto alla carica. Poi soglie di riconoscimento
  USB (col caricatore da muro la USB si deduce solo da tensione ≥ 4,1 V o salita tra cicli)
  e 10/30 %.
- **Hotspot senza backend raggiungibile**: oggi ogni ciclo via hotspot prova OTA e upload
  verso un IP LAN irraggiungibile. Si risolve col tunnel; eventualmente timeout più corti.
- **Spegnimento vero** (GPIO17 a 0) come alternativa al deep sleep per lunghi periodi.
- **Latenza di avvio**: beep a ~1,7 s dalla pressione. Il resto è bootloader (~0,4 s) e
  init; sotto il secondo solo con light sleep, da valutare dopo le misure di consumo.
- **Wake giornaliero a coda vuota** per OTA e ora (il timer di sync oggi si arma solo con
  file in coda), da valutare con le misure di consumo.
- Provisioning Wi-Fi, URL e token senza ricompilare (NVS/BLE); service token di Cloudflare
  Access come seconda barriera.
- **Doppia pressione di PWR** come trigger DEV alternativo a USER, se il gesto con GPIO0
  risulta scomodo.
- Backend: allineare `X-Capture-Ts` (UTC) al fuso locale nelle note; batteria nel digest.
