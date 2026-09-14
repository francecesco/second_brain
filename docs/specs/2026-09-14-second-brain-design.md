# Second Brain — Design / Spec

**Data:** 2026-09-14
**Stato:** approvato per la stesura del piano di implementazione. La programmazione
inizia all'arrivo dell'hardware (ordinato su Amazon).

---

## 1. Obiettivo

Un dispositivo personale a e-Paper basato su **Waveshare ESP32-S3-ePaper-1.54**
che funge da **satellite di cattura e visualizzazione** per un "second brain",
il cui cervello (storage, trascrizione, memoria, ragionamento) vive nel backend
di casa. Il contenuto è archiviato come **vault Obsidian** (file Markdown), così
il "cervello" è navigabile, collegabile e portabile senza lock-in. Tre funzioni:

1. **Cattura vocale rapida** — premi, parli, l'audio viene trascritto e archiviato
   come nota Markdown nel vault.
2. **Diario / journaling** — prompt giornalieri e riflessioni tracciate nel tempo
   (mappano sulle Daily Notes di Obsidian).
3. **Display glanceable** — informazioni a colpo d'occhio sul e-Paper (digest).

Esplicitamente **NON** un assistente conversazionale a bassa latenza: il device è
un dispositivo di input/output, non fa Q&A vocale in tempo reale.

## 2. Hardware (verificato)

Waveshare **ESP32-S3-ePaper-1.54** (variante B/N):

| Componente | Dettaglio |
|---|---|
| MCU | ESP32-S3 dual-core LX7 @240 MHz, 8 MB Flash, 8 MB PSRAM |
| Display | e-Paper 1,54" 200×200 B/N (persiste l'immagine senza corrente) |
| Audio | codec **ES8311** (I2S) + microfono + speaker onboard |
| Sensori | **SHTC3** (temp/umidità), RTC **PCF85063** |
| Storage/IO | slot microSD (FAT32), USB-C, connettore LiPo (MX1.25 2PIN) |
| Radio | Wi-Fi 2.4 GHz, BLE 5 |
| Tasti | PWR, BOOT (tasto utente da confermare in bring-up) |
| SDK | ESP-IDF + Arduino; [repo Waveshare](https://github.com/waveshareteam/ESP32-S3-ePaper-1.54G) |

## 3. Decisioni architetturali (approvate)

- **Ruolo del device:** portatile, uso a batteria, **sync differito**. Cattura anche
  offline; sincronizza quando torna sul Wi-Fi di casa.
- **Backend:** nuovo servizio dedicato **`secondbrain`** in Docker su
  ZimaBoard/CasaOS. È il **proprietario del processo** (ingest, STT, indice, digest)
  e l'unico interlocutore del device.
- **Fonte di verità del contenuto:** **vault Obsidian** (cartella di file Markdown)
  su ZimaBoard. **SQLite/FTS5** è un **indice derivato**, ricostruibile dal vault.
- **STT:** **locale** su ZimaBoard con `faster-whisper` (privacy, nessuna dipendenza cloud).
- **PiAgent:** coinvolto **solo on-demand via API** dal `secondbrain` per il
  ragionamento (tag, action items, prompt diario, review). Il device non parla mai
  direttamente col PiAgent. L'arricchimento viene scritto **dentro il file `.md`**.
- **Firmware:** modello **"record & forward"** — registra WAV su microSD e sincronizza;
  nessuno streaming audio in tempo reale. Il device non conosce il vault: invia solo audio.
- **Display:** il device **non renderizza layout**; scarica un **BMP 200×200 1-bit
  già renderizzato lato server** e lo mostra. Il layout si itera lato server senza
  riflashare. L'ultimo digest resta visibile offline grazie alla persistenza e-Paper.
- **Sync del vault verso i dispositivi (Obsidian mobile/desktop):** **Syncthing**
  self-hosted su ZimaBoard (coerente col CasaOS, nessun abbonamento).
- **Stack backend:** **Python / FastAPI + SQLite (FTS5) + faster-whisper**, un solo
  `docker-compose.yml`.

## 4. Architettura

```
┌─────────────────────────┐        Wi-Fi casa (sync differito)
│  e-Paper device (ESP32)  │  ───────────────────────────────┐
│  cattura audio → microSD │                                  ▼
│  mostra digest cache-ato │        ┌──────────────────────────────────────┐
└─────────────────────────┘        │  secondbrain  (Docker su ZimaBoard)    │
        ▲   registra offline        │  • FastAPI: /captures /digest /notes    │
        │   sincronizza online      │  • faster-whisper (STT locale)          │
        └───────────────────────────│  • vault writer  → scrive .md           │
                                     │  • file-watcher  → reindicizza          │
                                     │  • SQLite/FTS5 (INDICE derivato)        │
                                     │  • render digest 200×200 1-bit          │
                                     └───┬───────────────────────┬────────────┘
                    scrive/legge .md     │                       │ API on-demand
                                         ▼                       ▼
                          ┌──────────────────────────┐  ┌────────────────────────┐
                          │  Vault Obsidian (cartella │  │  PiAgent (LLM/agente):  │
                          │  Markdown, fonte di verità│  │  arricchimento, action  │
                          │  + Syncthing → device)    │  │  items, prompt, review  │
                          └──────────────────────────┘  └────────────────────────┘
                                     ▲
                          Obsidian su telefono/laptop (via Syncthing)
```

Il **vault** è la fonte di verità del contenuto. Il **`secondbrain`** ci scrive le
note (da audio trascritto) e le rilegge; **SQLite** è solo l'indice per ricerca e
stato. Il **PiAgent** viene chiamato on-demand e i suoi risultati finiscono nel `.md`.

## 5. Firmware (ESP-IDF) — "record & forward"

Unità con responsabilità singole. Il device è agnostico rispetto al vault:

- **Capture:** tasto utente tenuto premuto → registra WAV **16 kHz mono** da ES8311
  (I2S) su microSD, con timestamp dall'RTC. Feedback su e-Paper ("● Rec 0:07 →
  salvato"). Nessuna dipendenza dalla rete al momento della cattura.
- **Queue:** ogni cattura = file WAV su SD + riga in `queue.jsonl` (id, ts, durata, stato).
- **Sync:** al rilevamento del Wi-Fi di casa, `POST /captures` di ogni elemento in
  coda; alla conferma (ack) cancella il file e aggiorna la coda. Robusto ai riavvii
  (idempotenza lato server via id cattura).
- **Display:** `GET /digest.bmp` → scarica BMP 200×200 1-bit → blit su e-Paper →
  salva copia in cache su SD. Se offline, mostra l'ultima cache.
- **Power:** deep sleep tra le operazioni, wake su tasto; Wi-Fi acceso solo nella
  finestra di sync. Tensione batteria letta e inviata al server (mostrata nel digest).

**Da confermare in bring-up (Fase 0):** GPIO del tasto utente, controller e-Paper e
supporto refresh parziale, pin I2S ES8311, lettura tensione batteria.

## 6. Backend `secondbrain`

- **Stack:** FastAPI (Python) + SQLite/FTS5 + `faster-whisper` + vault writer/watcher.
  Un `docker-compose.yml`. Il container monta la cartella del vault come volume.
- **Endpoint:**
  - `POST /captures` — riceve WAV (+ id, ts, batteria) → Whisper trascrive →
    **scrive un nuovo file `.md` nel vault** (con frontmatter) → indicizza in SQLite →
    (opzionale) chiama PiAgent per tag/action items → risponde ack. Idempotente sull'id.
  - `GET /digest.bmp` — genera al volo l'immagine glanceable 200×200 1-bit:
    prossimo impegno, task del giorno, ultima nota, prompt diario, temp/umidità
    (SHTC3), batteria, ora (RTC).
  - `GET /notes?q=` — ricerca full-text sull'indice FTS5.
  - `POST /journal` (interno/scheduler) — genera/serve il prompt del diario del giorno
    e prepara/aggiorna la Daily Note.
- **Vault writer:** scrive file Markdown atomici (write-temp + rename). Le catture
  creano sempre file **nuovi** → nessun conflitto. L'arricchimento tocca solo il
  frontmatter o una sezione appesa (`## Action items`), rileggendo il file prima.
- **File-watcher + reindex:** osserva il vault; quando un file cambia (anche da
  Obsidian, a mano), aggiorna l'indice SQLite/FTS. Comando `reindex` per ricostruire
  l'indice da zero scandendo il vault.
- **Integrazione PiAgent (on-demand):** arricchimento nota, estrazione action items
  da spingere nei canali esistenti (Telegram/task del PiAgent), prompt giornaliero del
  diario, review settimanale. Timeout e fallback: se il PiAgent non risponde, la nota
  resta comunque salvata e trascritta nel vault.

## 7. Vault Obsidian: struttura e formato nota

Layout cartelle (dentro il vault):

```
Vault/
├── Captures/      # catture vocali rapide, un file per cattura
├── Daily/         # diario, un file per giorno (Daily Notes)
├── _audio/        # WAV originali referenziati dalle note (opzionale)
└── .obsidian/     # config Obsidian (non toccata dal backend)
```

Formato di una cattura (`Captures/2026-09-14-1523-spesa.md`):

```markdown
---
id: cap_20260914_1523
type: capture
created: 2026-09-14T15:23:00+02:00
tags: [inbox]
source: device
audio: _audio/cap_20260914_1523.wav
---
Testo trascritto della cattura, con eventuali [[wikilink]] e #tag.
```

Daily Note (`Daily/2026-09-14.md`) contiene il prompt del diario del giorno e le
risposte/riflessioni. Convenzioni Obsidian (`[[wikilink]]`, `#tag`, frontmatter)
abilitano backlink e graph view senza codice aggiuntivo.

## 8. Data model — SQLite (indice derivato)

Non è la fonte di verità: è ricostruibile dal vault via `reindex`.

```
notes(
  id            TEXT PRIMARY KEY,     -- id cattura o generato
  path          TEXT,                 -- percorso relativo del .md nel vault
  type          TEXT,                 -- 'capture' | 'journal'
  created_at    TEXT,                 -- ISO8601 (da RTC del device)
  tags          TEXT,                 -- JSON
  audio_ref     TEXT,                 -- riferimento WAV
  enriched      INTEGER,              -- 0/1: arricchimento PiAgent applicato
  mtime         TEXT                  -- per il watcher/reindex
)
notes_fts  -- tabella FTS5 sul corpo del .md
sync_state(id, received_at)  -- idempotenza delle catture dal device
```

Ricerca semantica / embeddings: **fuori scope ora** (YAGNI), eventuale Fase 5.

## 9. Sync del vault (Syncthing)

Il vault su ZimaBoard è condiviso con i dispositivi personali (telefono/laptop con
Obsidian) tramite **Syncthing** self-hosted. Nessun abbonamento, dati solo tra i tuoi
dispositivi. Alternative valutate e scartate per ora: Obsidian Sync (a pagamento),
Git (attriti coi merge automatici sui `.md`).

## 10. Concorrenza uomo↔macchina

- Catture del device → sempre file **nuovi** (nessun conflitto).
- Arricchimento PiAgent → scrittura atomica, tocca solo frontmatter/sezione appesa,
  rilegge il file prima di scrivere.
- Edit umani in Obsidian → il file-watcher reindicizza; il backend non sovrascrive
  contenuto scritto a mano.
- Syncthing gestisce i conflitti di sync tra device con file `*.sync-conflict-*`
  (rari, dato che il device non edita file esistenti).

## 11. Privacy & sicurezza

- STT, vault e indice **interamente in casa** (nessun audio verso il cloud).
- Il PiAgent è l'unico componente che può usare un LLM esterno, e solo su testo
  trascritto, on-demand.
- API `secondbrain` esposta solo sulla LAN di casa; token semplice device↔server.
- Syncthing cifra il traffico tra i dispositivi.

## 12. Fasi di implementazione

- **Fase 0 — Bring-up hardware:** flash, "hello" su e-Paper, registra un WAV su SD,
  leggi batteria, deep sleep. *Obiettivo: conoscere la scheda reale e confermare i
  "da confermare" della sezione 5.*
- **Fase 1 — Cattura → vault:** device registra→SD→sync; `secondbrain` minimo
  (POST audio → Whisper → **file `.md` nel vault** + indice SQLite). *La cattura
  vocale finisce come nota Markdown apribile in Obsidian.*
- **Fase 2 — Display glanceable + Daily Notes:** render server-side del digest +
  cache offline; prompt del diario nella Daily Note.
- **Fase 3 — Integrazione PiAgent:** arricchimento (tag/action items nel `.md`),
  review settimanale, ricerca FTS.
- **Fase 4 — Sync vault:** Syncthing tra ZimaBoard e dispositivi; file-watcher/reindex
  per gli edit fatti a mano in Obsidian.
- **Fase 5 (opzionale):** embeddings / ricerca semantica.

## 13. Non-goals (YAGNI, per ora)

- Assistente conversazionale vocale in tempo reale.
- Rendering di layout/UI complessi on-device.
- Ricerca semantica / embeddings (rimandata a Fase 5).
- Plugin Obsidian custom (usiamo solo Markdown + convenzioni standard).

## 14. Struttura del repo

```
second_brain/
├── docs/specs/         # questo documento
├── firmware/           # progetto ESP-IDF del device
└── backend/            # servizio secondbrain (FastAPI + docker-compose)
```
