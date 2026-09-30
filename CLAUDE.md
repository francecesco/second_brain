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
  (l'eseguibile non termina da solo). Al 2026-09-28 sono 30 e devono restare verdi.
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

## Come si lavora con l'hardware

- Scheda: Waveshare ESP32-S3 e-Paper 1.54" **B/N V2** (SSD1681, SPI2). Non è la "1.54G"
  a 4 colori: usare il suo repo per driver/pin ha fatto fallire il primo bring-up.
- Porta seriale sul Mac: `/dev/cu.usbmodem1101`. **In deep sleep la porta sparisce**;
  per flashare via cavo il device deve essere in DEV MODE (PWR, rilascio, USER entro 1 s).
  Senza cavo dati (USB solo alimentazione) si lavora via Wi-Fi: `POST /ota` per il push,
  `GET /status` per lo stato, OTA pull pubblicando un manifest sul server di test.
- ESP-IDF v5.3.1 in `~/esp/esp-idf`: `. ~/esp/esp-idf/export.sh` prima di `idf.py`.
- Server sul Mac: dal 2026-09-29 il device carica sul backend vero (`cd backend && docker
  compose up -d`, porta 8000; vedi `backend/README.md`). `tools/capture_server.py` resta
  come server di prova minimale, ma non va avviato insieme al backend (stessa porta).
  Il device deve avere in `secrets.h` il base URL del Mac **su quella rete** (a casa
  `192.168.1.28`, in ufficio `192.168.0.244` il 2026-09-29, cambia col DHCP).
- Rete di casa: due AP con lo stesso SSID FASTWEB; quello con BSSID `cc:2d:21:5f:59:29`
  non inoltra TCP (ping ok, connessioni in timeout in entrambe le direzioni). Per questo
  la voce di casa in `secrets.h` forza il BSSID del router `54:78:f0:bd:65:eb`.
- Rete dell'ufficio ("Advenias"): più AP, associazione in 1–2 s; con budget di 8 s la
  connessione a volte non arrivava, ora è 12 s dopo la scansione.
- Non c'è batteria collegata: staccando la USB il device si spegne e **l'RTC si azzera**
  (NTP lo risistema al primo sync). Un taglio di alimentazione durante la registrazione
  è il test di recupero dei `.part`.
- Trappola OTA: se sul server resta pubblicata una versione più alta di quella flashata
  (`secondbrain firmware list` sul backend, `ota_serve/firmware/manifest.json` con
  `capture_server.py`), il device si aggiorna a quel binario al primo ciclo. Dopo un flash
  via USB, `secondbrain firmware rollback` o pubblicare la versione flashata.
- OTA col backend: `docker compose cp <bin> app:/tmp/fw.bin` e `docker compose exec app
  secondbrain firmware publish /tmp/fw.bin --version X.Y.Z`; il manifest è generato
  dalla release corrente.
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
  reale (ms dall'avvio dell'app), picco audio, byte e tempo di upload.
- Il refresh completo dell'e-Paper "lampeggia" in nero per ~2 s: è il pannello, non un
  bug. Il refresh parziale non è implementato.
- Per riavviare un device fermo in DEV MODE: pressione di PWR (dal firmware 0.4.0), oppure
  push di un firmware, oppure `esptool ... --after hard_reset chip_id` con il cavo dati.

## Stato al 2026-09-30

- Tutto su `master`, compreso il backend (`backend-fase1b` e `backend-elaborazione-ai` uniti).
  `origin` (`github.com/francecesco/second_brain`, pubblico) è fermo alla Fase 1a: il
  push lo fa l'autore.
- **Hardware e firmware: chiusi**, in attesa della batteria. Firmware sul device: 0.6.3
  (build di test del codice corrente, installata via OTA dal backend; `version.txt` in
  git resta 0.1.0), conosce rete di casa e ufficio.
- Batteria: da acquistare con connettore MX1.25 2 pin (polarità da verificare). All'arrivo:
  prova di mantenimento (acceso dopo il rilascio di PWR e dopo il deep sleep), poi misure
  e taratura (vedi Idee future).
- **Backend Fase 1b** (`backend/`, spec `docs/specs/2026-09-28-backend-archivio-design.md`):
  implementato e verificato col device vero sul Mac (upload, finder, OTA). Gira sul Mac
  in Docker sulla porta 8000 con `ALLOW_UNAUTHENTICATED_LAN=true`; `backend/.env` e
  `backend/data/` sono locali e gitignored. Manca il Task 14: deploy sulla ZimaBoard.
- **Elaborazione AI** (spec `docs/specs/2026-09-29-backend-elaborazione-ai-design.md`):
  trascrizione, titolo, riassunto e tag con Groq e Gemini (chiavi inserite dall'autore
  nella pagina Impostazioni, cifrate con `SETTINGS_KEY` del `backend/.env` del Mac), worker
  separato nel compose, ricerca full-text. Verificata sul Mac il 2026-09-30; alcuni test
  end-to-end rimandati (vedi "Stato" della spec). Unita in `master` il 2026-09-30.
- Le catture di prova di `capture_server.py` sono state cancellate il 2026-09-29.

## Prossima sessione

1. **Deploy sulla ZimaBoard** (Task 14 del piano `docs/plans/2026-09-28-backend-archivio.md`),
   da casa. Servono dall'autore: dominio Cloudflare e hostname (proposti `brain.<dominio>`
   per la UI, `ingest.<dominio>` per i dispositivi), `utente@host` SSH, cartella dati
   (proposta `/DATA/AppData/secondbrain`). Verifiche: `X-Forwarded-Proto` dal tunnel,
   Bot Fight Mode spento sull'hostname dei dispositivi, IP reale del device nei log (non
   `172.x`), `401` senza token dal tunnel, login da rete mobile. Poi `secrets.h` punta
   alla ZimaBoard per la rete di casa.
   Con l'elaborazione AI: sulla ZimaBoard va generata una `SETTINGS_KEY` nuova
   (`secondbrain gen-key`) e le chiavi API vanno reinserite dalla pagina Impostazioni; l'archivio
   va su `/mnt/Storage1` (l'eMMC di sistema ha 7 GB liberi).
2. **Test end-to-end rimandati dell'elaborazione AI** (elenco nello "Stato" della spec):
   fallback con chiave sbagliata, stop del worker durante una trascrizione, pausa,
   correzione + Rielabora, ricerca dal telefono, cestino durante l'elaborazione, rescan.
3. **Firmware "HTTPS + token"** (spec separata, punti in §11 della spec backend): prima
   di tutto 401/403/429 come errori temporanei (oggi ogni 4xx manda il file in
   `rejected/`), poi TLS e token. Da provare col tunnel attivo.

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

## Idee future e cose rimandate

- **Backend dopo l'elaborazione AI**: adattatore locale (faster-whisper o altro) passando a
  un NAS più potente; divisione in pezzi dell'audio oltre i limiti dei provider; vista per
  tag; note nel vault Obsidian (i `.md` sono già nel formato giusto); arricchimento con il
  PiAgent.
- **Fase 2 — display glanceable**: `GET /digest.bmp` 200×200 1-bit renderizzato dal
  server, cache su SD, mostrato al posto della schermata di stato.
- **Refresh parziale e-Paper**: contatore che scorre durante la registrazione, meno
  lampeggi.
- **Batteria** (in arrivo): verificare per prima cosa che il device resti acceso al
  rilascio di PWR e dopo il deep sleep (mantenimento GPIO17); poi consumo in deep sleep e
  per ciclo, autonomia, curva di scarica reale da sostituire in `battery_policy.c`, soglie
  di riconoscimento USB e 10/30 %. Con la batteria l'RTC non si azzera più.
- **Spegnimento vero** (GPIO17 a 0) come alternativa al deep sleep per lunghi periodi.
- **Latenza di avvio**: beep a ~1,7 s dalla pressione. Il resto è bootloader (~0,4 s) e
  init; sotto il secondo solo con light sleep, da valutare dopo le misure di consumo.
- **Timer periodico di sync** senza cattura (oggi il wake è solo da tasto).
- **HTTPS** verso il backend reale; provisioning Wi-Fi senza ricompilare (NVS/BLE).
- **Doppia pressione di PWR** come trigger DEV alternativo a USER, se il gesto con GPIO0
  risulta scomodo.
- Backend: allineare `X-Capture-Ts` (UTC) al fuso locale nelle note; batteria nel digest.
