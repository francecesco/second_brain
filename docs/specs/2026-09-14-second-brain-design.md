# Second Brain — Design / Spec

**Data:** 2026-09-14
**Stato:** approvato per la stesura del piano di implementazione. La programmazione
inizia all'arrivo dell'hardware (ordinato su Amazon).

---

## 1. Obiettivo

Un dispositivo personale a e-Paper basato su **Waveshare ESP32-S3-ePaper-1.54**
che funge da **satellite di cattura e visualizzazione** per un "second brain",
il cui cervello (storage, trascrizione, memoria, ragionamento) vive nel backend
di casa. Tre funzioni:

1. **Cattura vocale rapida** — premi, parli, l'audio viene trascritto e archiviato.
2. **Diario / journaling** — prompt giornalieri e riflessioni tracciate nel tempo.
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
  ZimaBoard/CasaOS. È il **proprietario dei dati** e l'unico interlocutore del device.
- **STT:** **locale** su ZimaBoard con `faster-whisper` (privacy, nessuna dipendenza cloud).
- **PiAgent:** coinvolto **solo on-demand via API** dal `secondbrain` per il
  ragionamento (tag, action items, prompt diario, review). Il device non parla mai
  direttamente col PiAgent.
- **Firmware:** modello **"record & forward"** — registra WAV su microSD e sincronizza;
  nessuno streaming audio in tempo reale.
- **Display:** il device **non renderizza layout**; scarica un **BMP 200×200 1-bit
  già renderizzato lato server** e lo mostra. Il layout si itera lato server senza
  riflashare. L'ultimo digest resta visibile offline grazie alla persistenza e-Paper.
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
        │   sincronizza online      │  • SQLite + FTS5 (note, journal, tag)   │
        └───────────────────────────│  • faster-whisper (STT locale)          │
                                     │  • render digest 200×200 1-bit          │
                                     └───────────────┬────────────────────────┘
                                                     │ API on-demand
                                                     ▼
                                     ┌──────────────────────────────────────┐
                                     │  PiAgent (LLM/agente): arricchimento,  │
                                     │  action items, prompt diario, review   │
                                     └──────────────────────────────────────┘
```

## 5. Firmware (ESP-IDF) — "record & forward"

Unità con responsabilità singole:

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

- **Stack:** FastAPI (Python) + SQLite/FTS5 + `faster-whisper`. Un `docker-compose.yml`.
- **Endpoint:**
  - `POST /captures` — riceve WAV (+ id, ts, batteria) → Whisper trascrive →
    salva nota → (opzionale) chiama PiAgent per tag/action items → risponde ack.
    Idempotente sull'id cattura.
  - `GET /digest.bmp` — genera al volo l'immagine glanceable 200×200 1-bit:
    prossimo impegno, task del giorno, ultima nota, prompt diario, temp/umidità
    (SHTC3), batteria, ora (RTC).
  - `GET /notes?q=` — ricerca full-text (FTS5).
  - `POST /journal` (interno/scheduler) — genera/serve il prompt del diario del giorno.
- **Integrazione PiAgent (on-demand):** arricchimento nota (tag, riassunto),
  estrazione action items da spingere nei canali esistenti (Telegram/task del
  PiAgent), prompt giornaliero del diario, review settimanale. Chiamate HTTP verso
  l'API del PiAgent; timeout e fallback (se il PiAgent non risponde, la nota resta
  comunque salvata e trascritta).

## 7. Data model (SQLite)

```
notes(
  id            TEXT PRIMARY KEY,     -- id cattura (dal device) o generato
  type          TEXT,                 -- 'capture' | 'journal'
  created_at    TEXT,                 -- ISO8601 (da RTC del device)
  transcript    TEXT,
  tags          TEXT,                 -- CSV o JSON
  audio_ref     TEXT,                 -- path/riferimento WAV archiviato
  enriched_json TEXT,                 -- output PiAgent (action items, riassunto)
  source        TEXT                  -- 'device'
)
notes_fts  -- tabella FTS5 su transcript
```

Ricerca semantica / embeddings: **fuori scope ora** (YAGNI), eventuale Fase 4.

## 8. Privacy & sicurezza

- STT e storage **interamente in casa** (nessun audio verso il cloud).
- Il PiAgent è l'unico componente che può usare un LLM esterno, e solo su testo
  trascritto, on-demand.
- API `secondbrain` esposta solo sulla LAN di casa; token semplice device↔server.

## 9. Fasi di implementazione

- **Fase 0 — Bring-up hardware:** flash, "hello" su e-Paper, registra un WAV su SD,
  leggi batteria, deep sleep. *Obiettivo: conoscere la scheda reale e confermare i
  "da confermare" della sezione 5.*
- **Fase 1 — Cattura end-to-end:** device registra→SD→sync; `secondbrain` minimo
  (POST audio → Whisper → nota in SQLite). *La cattura vocale funziona.*
- **Fase 2 — Display glanceable:** render server-side del digest + cache offline;
  prompt del diario.
- **Fase 3 — Integrazione PiAgent:** arricchimento, action items, review
  settimanale, ricerca FTS.
- **Fase 4 (opzionale):** embeddings / ricerca semantica.

## 10. Non-goals (YAGNI, per ora)

- Assistente conversazionale vocale in tempo reale.
- Rendering di layout/UI complessi on-device.
- Ricerca semantica / embeddings (rimandata a Fase 4).
- Sincronizzazione multi-dispositivo o cloud di terze parti.

## 11. Struttura del repo

```
second_brain/
├── docs/specs/         # questo documento
├── firmware/           # progetto ESP-IDF del device
└── backend/            # servizio secondbrain (FastAPI + docker-compose)
```
