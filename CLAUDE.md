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
- Messaggi di commit in italiano, prefisso `firmware:` o `docs:`, corpo che spiega il
  perché e, se c'è, cosa è stato verificato sull'hardware.
- Il remote GitHub lo apre l'autore quando vuole: non creare repo né remote di iniziativa.
- Branch di lavoro per fase (`firmware-fase0`, `firmware-fase1a`); merge in `master` su
  decisione dell'autore. Il branch principale si chiama `master`.

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
- Server di test sul Mac: `python3 tools/capture_server.py` (porta 8000, `POST /captures`
  + `GET /firmware/*` da `ota_serve/firmware/`). Il device deve avere in `secrets.h` il
  base URL del Mac **su quella rete** (a casa `192.168.1.28`, in ufficio `192.168.0.157`,
  può cambiare col DHCP).
- Rete di casa: due AP con lo stesso SSID FASTWEB; quello con BSSID `cc:2d:21:5f:59:29`
  non inoltra TCP (ping ok, connessioni in timeout in entrambe le direzioni). Per questo
  la voce di casa in `secrets.h` forza il BSSID del router `54:78:f0:bd:65:eb`.
- Rete dell'ufficio ("Advenias"): più AP, associazione in 1–2 s; con budget di 8 s la
  connessione a volte non arrivava, ora è 12 s dopo la scansione.
- Non c'è batteria collegata: staccando la USB il device si spegne e **l'RTC si azzera**
  (NTP lo risistema al primo sync). Un taglio di alimentazione durante la registrazione
  è il test di recupero dei `.part`.
- Trappola OTA: se in `ota_serve/firmware/manifest.json` resta una versione più alta di
  quella flashata, il device si aggiorna a quel binario al primo ciclo. Dopo un flash via
  USB, rigenerare o rimuovere il manifest.
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

## Stato al 2026-09-28 (fine giornata)

- Tutto su `master`, allineato con `origin` (`github.com/francecesco/second_brain`, pubblico).
- **Hardware e firmware: chiusi**, in attesa della batteria. Collaudo completo superato il
  2026-09-28 (vedi Storico). Firmware sul device: 0.6.2 (build di test del codice corrente;
  `version.txt` in git resta 0.1.0), coda vuota, conosce rete di casa e ufficio.
- Batteria: da acquistare con connettore MX1.25 2 pin (polarità da verificare). All'arrivo:
  prova di mantenimento (acceso dopo il rilascio di PWR e dopo il deep sleep), poi misure
  e taratura (vedi Idee future).
- Sul Mac di casa (192.168.1.28) gira `tools/capture_server.py` sulla porta 8000; le
  catture di prova sono in `firmware/captures_inbox/` e `captures_inbox_old/` (gitignored).
- `backend/` vuoto.

## Prossima sessione: Fase 1b, backend `secondbrain`

1. Rileggere `docs/specs/2026-09-14-second-brain-design.md` §6–8 e il piano
   `docs/plans/2026-09-14-secondbrain-backend.md` (10 task TDD, mai eseguito).
2. Riallineare piano e spec al contratto reale del device
   (`docs/specs/2026-09-23-firmware-capture-sync-design.md` §6.2 e README): corpo WAV
   grezzo, metadati negli header `X-Capture-*`, `X-Device-Id`, `X-Firmware-Version`,
   `X-Battery-*`, `X-Power-Source`; `201` accettata, `409` duplicato; più
   `GET /firmware/manifest.json` per l'OTA. Il piano originale parlava di multipart.
3. Decisioni da prendere con l'autore all'inizio: dove gira durante lo sviluppo (Mac o
   subito ZimaBoard/CasaOS in Docker), modello faster-whisper e lingua, formato e
   posizione delle note nel vault, cosa fare dell'audio originale.
4. Workflow come per il firmware: spec approvata a sezioni → piano a task → esecuzione
   con test, commit piccoli, verifica end-to-end con il device vero.

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

## Idee future e cose rimandate

- **Fase 1b — backend** `secondbrain`: implementare il contratto `POST /captures` di
  `docs/specs/2026-09-23-firmware-capture-sync-design.md` §6.2 (idempotente su
  `X-Capture-Id`, `409` sui duplicati), trascrizione con faster-whisper, nota `.md` con
  frontmatter nel vault, indice SQLite/FTS5, endpoint `GET /firmware/manifest.json` per
  l'OTA. Il piano TDD esiste già; va allineato al contratto header-based (la spec di
  progetto parlava di multipart).
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
