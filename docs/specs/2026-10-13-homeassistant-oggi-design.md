# Home Assistant e schermata "Oggi" sul device — Design / Spec

**Data:** 2026-10-13
**Stato:** approvata in chat a sezioni il 2026-10-13, da pianificare.

## 1. Obiettivo

Collegare il backend a Home Assistant per leggere calendari, sensori e lista della spesa, e
mostrarli sul device in una schermata "Oggi". È il primo di tre sottoprogetti, nell'ordine
scelto dall'autore:

1. **questo**: connettore Home Assistant nel backend + schermata "Oggi" sul device
   (è la "Fase 2 — display glanceable" delle idee future);
2. pannello "Oggi" nella web app, sullo stesso connettore;
3. calendario e lista come contesto per l'elaborazione AI delle note.

La connessione si configura dalle Impostazioni (URL, token, entità scelte): niente nomi
fissi nel codice, così il progetto è utilizzabile anche da altri.

Riuscita: al risveglio il device mostra gli eventi di oggi e domani, gli articoli della
spesa e i sensori scelti; ridisegna solo quando qualcosa cambia; senza rete o senza Home
Assistant mostra l'ultimo dato buono dicendo di quando è.

## 2. Decisioni (approvate in chat il 2026-10-13)

1. Sezione **"Connessione Home Assistant"** nelle Impostazioni: URL, token di lunga durata,
   calendari da condividere, sensori da esporre (con etichetta breve), lista della spesa.
   Le entità si scelgono da quelle che Home Assistant elenca, non si scrivono a mano.
2. **Il backend disegna la parte centrale come immagine 1 bit, il device aggiunge
   intestazione e piede** (approccio A). Scartati: JSON disegnato dal firmware (B: ogni
   modifica di layout richiede un firmware, accenti e tagli in C) e righe pre-impaginate
   (C: due copie delle metriche del font).
3. Il worker legge Home Assistant ogni 5 minuti e tiene un'istantanea; il device la riceve
   dalla cache, senza aspettare Home Assistant.
4. Solo lettura verso Home Assistant in questo sottoprogetto.
5. Riferimento: i plugin `piagent-calendario` e `piagent-lista-spesa` del PiAgent usano già
   `calendar.get_events` e `todo.get_items` con `return_response`: stesse chiamate.

## 3. Impostazioni (backend)

- **Dati**: nella tabella delle impostazioni, chiave `homeassistant`: `url`, `calendars`
  (lista di `entity_id`), `sensors` (lista di `{entity_id, label}`), `shopping_list`
  (`entity_id` o null). Token cifrato con `SETTINGS_KEY` (`SecretBox`, come le chiavi dei
  provider AI), chiave `homeassistant_token`; mostrato solo mascherato (`mask_key`), si
  sostituisce o si cancella, mai si rilegge. Senza `SETTINGS_KEY` la card spiega che non si
  può salvare.
- **Card "Connessione Home Assistant"** in `/settings`:
  1. URL (solo `http`/`https`) e token, "Salva" e "Prova". "Prova" chiama `GET /api/`
     (risposta "API running") e mostra ok o l'errore (401, timeout, certificato…).
  2. Dopo una prova riuscita, scelta delle entità da `GET /api/states`, a caselle, con nome
     amichevole ed `entity_id`: calendari (`calendar.*`, multipla), sensori (`sensor.*`,
     multipla, campo etichetta accanto; vuota = nome amichevole), lista della spesa
     (`todo.*`, singola, con "nessuna"). Le entità salvate che Home Assistant non elenca
     più restano visibili come "non trovata".
  3. Ultima lettura (ora, numero di elementi, ultimo errore) e "Aggiorna ora".
  4. Anteprima dell'immagine per il device (PNG ingrandito).
- Login e CSRF come le altre impostazioni; il token non compare mai in pagine, log o
  messaggi d'errore (filtro `redact`).

## 4. Connettore (`homeassistant.py`)

- `httpx`, timeout 10 s per chiamata, header `Authorization: Bearer <token>`.
- `test(url, token)`, `list_entities(url, token)`, `read(config, token, now, tz)`.
- Lettura:
  - calendari: `POST /api/services/calendar/get_events?return_response` con le entità
    scelte, da adesso a fine domani (fuso dell'archivio);
  - lista: `POST /api/services/todo/get_items?return_response`, stato `needs_action`;
  - sensori: `GET /api/states/<entity_id>`, valore e `unit_of_measurement`.
- Un'entità che non esiste più si salta (le altre si leggono) e viene segnalata; una risposta
  senza `return_response` (Home Assistant prima del 2023.12) dà un errore chiaro.

## 5. Cache e immagine (backend)

- **Istantanea**: il worker la aggiorna ogni `HA_REFRESH_S = 300` s e subito dopo un
  salvataggio o "Aggiorna ora"; chiave `homeassistant_snapshot` con ora della lettura,
  eventi, articoli, sensori, ultimo errore con ora. Una lettura fallita conserva i dati
  precedenti e registra solo l'errore. Senza configurazione non si legge.
- **Endpoint `GET /digest/today`**, autenticato col token del device (come il manifest OTA):
  - `200`: immagine 1 bit grezza **200×136**, formato del framebuffer del firmware (bit 1 =
    nero, righe impacchettate MSB first, 3.400 byte), `ETag` = hash del contenuto;
  - `304` con `If-None-Match` uguale;
  - `204` se Home Assistant non è configurato o non c'è mai stata una lettura.
- **Disegno** (`digest.py`, Pillow, testo senza sfumature): Space Grotesk della UI convertito
  una volta in TTF e tenuto nel repo (licenza OFL). Righe in ordine di priorità, tagliate dal
  fondo se non c'è posto:
  1. **OGGI**: eventi non finiti, al massimo 3, prima quelli di tutto il giorno, poi
     `09:30 Titolo…` (taglio con "…");
  2. **DOMANI**: al massimo 2, prima quelli di tutto il giorno (es. differenziata);
  3. **SPESA**: `N articoli` o `vuota`;
  4. sensori `etichetta valore` separati da ` · ` (due per riga).
  "Oggi"/"domani" nel fuso dell'archivio. Istantanea più vecchia di 30 minuti: riga piccola
  `agg. HH:MM` in fondo.

## 6. Firmware

- **Ciclo**: dopo upload e OTA, con rete, `GET /digest/today` col token e
  `If-None-Match: <etag>` (etag in memoria RTC). `200`: immagine sull'heap, salvata su SD
  come `digest.bin` con l'etag; `304`: resta quella su SD; `204`: `digest.bin` cancellato,
  schermata di stato classica; errore: resta quella su SD.
- **Composizione** (`screen.c`, C puro con anteprima sul Mac): intestazione come oggi (ora e
  batteria, ~30 px), immagine del backend (136 px), piede su una riga con coda, rete ed esito
  (in negativo su fascia nera per gli esiti importanti, come l'esito di oggi).
  `screen_render_today(fb, &status, digest)`. Senza immagine valida: `screen_render_status`
  come oggi. DEV MODE, REC e messaggi brevi invariati.
- **Ridisegno**: ai wake dal timer se cambia lo stato mostrato (regole di oggi) **oppure**
  l'etag dell'immagine (funzione pura in `sync_retry_policy.c`, test sull'host); tasto sopra
  e registrazioni ridisegnano sempre. L'ora in alto resta quella dell'ultimo disegno.
- **Senza rete**: ultima immagine da SD, piede con "no wifi" o l'errore.
- Moduli: `digest.c` (scarica, salva, carica; `esp_err_t`), composizione in `screen.c`,
  decisione in `sync_retry_policy.c`. Costanti in `config.h` (altezza 136, offset,
  nome del file); buffer sull'heap.

## 7. Errori

| Caso | Comportamento |
|---|---|
| Token di Home Assistant sbagliato o scaduto (401) | "Prova" lo dice; il worker conserva l'istantanea e registra l'errore; il device mostra i dati con `agg. HH:MM` |
| Home Assistant spento o irraggiungibile | Come sopra; timeout 10 s, il worker non si blocca |
| Entità scelta che non esiste più | Saltata e segnata "non trovata"; le altre si leggono |
| Home Assistant senza `return_response` | Errore chiaro nella prova, istantanea intatta |
| `SETTINGS_KEY` mancante | La card dice che il token non si può salvare |
| Device senza token o con token sbagliato su `/digest/today` | `401`/`403` come OTA e upload, coda intatta |
| `digest.bin` di dimensione sbagliata | Ignorato e cancellato, schermata di stato |
| SD assente | Immagine solo se appena scaricata, altrimenti stato; esito "sd assente" |

## 8. Verifica

1. Backend (TDD, Home Assistant finto con `httpx.MockTransport`): connettore (prova ok/401/
   timeout, entità, lettura, entità mancante); impostazioni (token cifrato e mai in pagina,
   scelta delle entità ed etichette, CSRF); worker (aggiornamento, errore che conserva
   l'istantanea); immagine (dimensione, priorità e taglio delle righe, "…", `agg.`, fuso e
   mezzanotte); endpoint (auth, 200 con etag, 304, 204). Screenshot della card a 1280 e
   390 px contro `docs/stile-ui.md`.
2. Firmware: test host per la decisione di ridisegno, la validazione di `digest.bin` e la
   composizione; i test esistenti restano verdi. Anteprima sul Mac con un `digest.bin` vero.
3. Hardware: schermata "Oggi" dopo il tasto sopra; wake dal timer senza cambiamenti senza
   refresh (304 nel log); articolo aggiunto in Home Assistant → schermata cambiata al wake
   successivo; Home Assistant spento → `agg. HH:MM`; senza rete → ultima immagine; Home
   Assistant tolto dalle Impostazioni → schermata di stato.
4. Ordine: backend provato in locale sul Mac col Home Assistant dell'autore (URL e token li
   inserisce lui), poi firmware via cavo e release OTA; deploy sulla ZimaBoard solo dopo
   l'ok dell'autore.
