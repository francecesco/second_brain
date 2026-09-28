# Backend Fase 1b — Archivio delle registrazioni — Design / Spec

**Data:** 2026-09-28
**Stato:** design approvato a sezioni il 2026-09-28, spec da rivedere; piano da scrivere.
**Dipende da:** `docs/specs/2026-09-23-firmware-capture-sync-design.md` (contratto
`POST /captures` §6.2, implementato e verificato sul device).
**Sostituisce:** per la parte backend, §6–8 di `docs/specs/2026-09-14-second-brain-design.md`
e l'intero piano `docs/plans/2026-09-14-secondbrain-backend.md` (mai eseguito), che
restano come storico.

---

## 1. Obiettivo

Un servizio `secondbrain` installabile con Docker su un server di casa (oggi la
ZimaBoard, domani un NAS UGREEN o altro) che:

1. riceve le registrazioni dal device e-Paper (e in futuro da altri dispositivi, anche
   di tipo diverso) con il contratto già implementato nel firmware;
2. le archivia su disco in cartelle `anno/mese/giorno`, dove in seguito finiranno anche
   i file prodotti dall'AI per quelle registrazioni;
3. le rende consultabili e gestibili da una UI web tipo "finder", raggiungibile in LAN e
   da fuori casa attraverso un tunnel Cloudflare;
4. serve il firmware OTA ai dispositivi, al posto di `tools/capture_server.py`.

## 2. Cambi rispetto alla spec di progetto

| Spec 2026-09-14 | Questa spec | Perché |
|---|---|---|
| SQLite/FTS5 come indice | **Postgres** come catalogo | servizio installabile, più dispositivi, stesso motore in sviluppo e produzione |
| Vault Obsidian come fonte di verità, nota per cattura | **Archivio su disco** `AAAA/MM/GG/` come fonte di verità | Obsidian e l'AI arrivano dopo, come consumatori dell'archivio |
| STT e note nella stessa fase | solo archiviazione | un risultato utile subito, meno rischio |
| Upload multipart | WAV grezzo + header `X-*` | è il contratto reale del firmware |

## 3. Non-goals della Fase 1b

Trascrizione e AI in genere, integrazione Obsidian, ricerca testuale, upload manuale di
file dalla UI, creazione di cartelle, multi-utente, selezione multipla, backup
automatici, digest per il display. L'HTTPS e il token lato firmware sono una spec
firmware separata (§11).

## 4. Principio: i file sono la verità, Postgres è il catalogo

```
archive/
  2026/09/28/
    091530_70041dd8263c.wav
    091530_70041dd8263c.json             sidecar con tutti i metadati della registrazione
    091530_70041dd8263c.transcript.md    (futuro, AI) stesso nome base
  .incoming/                             upload in corso (<uuid>.tmp)
  .trash/2026/09/28/...                  cestino, con la struttura originale
```

- Ogni registrazione ha un **nome base** stabile `HHMMSS_<device-id>` (ora locale di
  registrazione, id del dispositivo), con `_2`, `_3`... in caso di collisione. Tutti i
  file derivati condividono il nome base; la UI li raggruppa come un solo elemento.
- Il **titolo non rinomina il file**: vive nei metadati. La correzione della data sposta i
  file in un'altra cartella del giorno, ma senza cambiarne il nome. Link, file derivati e
  riferimenti futuri non si rompono.
- Il sidecar `.json` contiene i campi della tabella `captures` (§8) tranne `rel_path` e
  `day`, più `schema_version`. Postgres si può ricostruire dal disco con `secondbrain rescan`.
- Ogni modifica dalla UI scrive prima su disco (sidecar, spostamenti), poi sul DB.
- Dispositivi, release firmware e utenti stanno solo in Postgres: sono configurazione,
  non archivio (backup con `pg_dump`).

## 5. Architettura e componenti

**Deploy:** `docker compose` con i servizi `app` (FastAPI) e `postgres`, più
`cloudflared` nel profilo opzionale `tunnel`. Volumi: `archive/`, `firmware/`, dati di
Postgres. Configurazione solo da variabili d'ambiente (`.env`). Immagini amd64 e arm64,
nessuna dipendenza da CasaOS.

**Configurazione** (valori predefiniti tra parentesi): `DATABASE_URL`, `ARCHIVE_DIR`
(`/data/archive`), `FIRMWARE_DIR` (`/data/firmware`), `TZ_ARCHIVE` (`Europe/Rome`),
`MAX_UPLOAD_BYTES` (32 MB), `TRASH_RETENTION_DAYS` (30), `ALLOW_UNAUTHENTICATED_LAN`
(`false`), `DEVICE_HOSTNAME` (vuoto = nessuna restrizione per hostname). Nessun segreto
di sessione: le sessioni sono righe in DB con id casuali.

**Codice:** pacchetto Python `secondbrain` in `backend/`, un modulo per responsabilità.

| Modulo | Responsabilità | Dipende da |
|---|---|---|
| `config` | lettura e validazione delle variabili d'ambiente | — |
| `naming` | funzioni pure: timestamp UTC + fuso → cartella `AAAA/MM/GG` e nome base; plausibilità del timestamp; suffissi di collisione | — |
| `wav` | funzione pura: validazione header, formato, durata | — |
| `archive` | scrittura atomica, sidecar, sposta, cestino, ripristino, pulizia `.incoming` | `naming` |
| `catalog` | modelli e query su Postgres, migrazioni Alembic | Postgres |
| `ingest` | `POST /captures` (§6) | `archive`, `catalog`, `wav` |
| `ota` | manifest e binari firmware (§9) | `catalog` |
| `web` | UI finder e login (§7) | `catalog`, `archive` |
| `cli` | `rescan`, `device add`, `set-password`, `trash purge`, `firmware publish/rollback` | tutti |

**Autenticazione per percorso:**
- `/captures` e `/firmware/*`: token del dispositivo, `Authorization: Bearer <token>`,
  salvato in DB come hash. Con `ALLOW_UNAUTHENTICATED_LAN=true` una richiesta senza
  token è accettata **solo se arriva dalla LAN**: IP del client privato o di loopback
  (IPv4 mappati in IPv6 compresi) **e** nessun header `Cf-Connecting-Ip`, che
  Cloudflare aggiunge sempre (sovrascrivendo quello del client) alle richieste passate
  dal tunnel. Il dispositivo sconosciuto si registra da `X-Device-Id`. Dal tunnel il
  token resta obbligatorio anche con l'opzione attiva. Serve finché il firmware non
  manda il token (§11): si può tenere acceso anche in produzione, al prezzo di
  accettare upload da chiunque sia sulla rete di casa; spento di default.
- Tutto il resto: sessione di login della UI.
- Se `DEVICE_HOSTNAME` è impostato, su quell'hostname (header `Host`, che `cloudflared`
  imposta all'hostname pubblico) rispondono solo `POST /captures` e `/firmware/*`.
  `X-Forwarded-Host` non viene usato: lo può falsificare chiunque.

## 6. Ricezione: `POST /captures`

Contratto del firmware (§6.2 della spec 2026-09-23), più `Authorization`.

1. **Autenticazione.** Token assente o non valido → `401`. `X-Device-Id` diverso dal
   dispositivo del token → `403`. Si aggiornano `last_seen_at`, firmware e batteria del
   dispositivo.
2. **Header.** `X-Capture-Id` deve corrispondere a `cap_AAAAMMGG_HHMMSS[_k]` o
   `cap_unsynced_NNNNNN[_k]`, altrimenti `400`. `Content-Length` obbligatorio (assente →
   `411`), oltre `MAX_UPLOAD_BYTES` → `413`; byte ricevuti diversi da `Content-Length` →
   `400`.
3. **Ricezione in streaming** su `archive/.incoming/<uuid>.tmp`, con sha256 calcolato
   durante la ricezione, poi `fsync`. Body troncato: si cancella il temporaneo (il device
   va in timeout e tiene il file).
4. **Validazione WAV** (RIFF/WAVE, PCM, durata > 0) → `422` se non valida.
5. **Deduplicazione** su `(device_id, capture_id)`, contando anche le registrazioni nel
   cestino (l'eliminazione definitiva cancella riga e file):
   - stesso id e stesso sha256 → `409` `{"id": ..., "status": "duplicate"}`, si cancella il
     temporaneo;
   - stesso id e contenuto diverso (contatore `unsynced` in NVS azzerato) → non è un
     duplicato: si accetta con un nuovo nome base e un warning nel log.
6. **Data.** `X-Capture-Ts` valido e plausibile (anno ≥ 2024 e non oltre 24 h nel futuro
   rispetto all'ora del server) → `recorded_at` = quel valore, `date_estimated = false`.
   Altrimenti `recorded_at` = ora di ricezione, `date_estimated = true`. La cartella e
   il nome base usano `recorded_at` convertito in `TZ_ARCHIVE`.
7. **Archiviazione**, in una transazione: insert della riga, scrittura del sidecar
   (tmp + rename), rename del WAV nella cartella del giorno, commit.
8. **Risposta** `201` `{"id": "<uuid>", "path": "2026/09/28/091530_70041dd8263c.wav",
   "status": "accepted"}`.

**Errori lato server:** disco pieno → `507`, DB non raggiungibile → `503`, altro →
`500`. Tutti 5xx: il device tiene il file e ritenta al ciclo successivo.

**Recupero:** all'avvio si cancellano i file in `.incoming/`. WAV senza riga o righe
senza file si riconciliano con `rescan`, che segnala le incongruenze nel log. Se il
server archivia ma la risposta non arriva al device, al ritentativo il device riceve
`409` e cancella il file: niente perso, niente duplicato.

## 7. UI finder

Una pagina renderizzata dal server (Jinja + htmx), usabile da desktop e da telefono.

- **Navigazione:** albero anno → mese → giorno con i conteggi (su telefono un menu a
  scomparsa), breadcrumb. Anno e mese mostrano la griglia dei figli con i conteggi, il
  giorno l'elenco delle registrazioni in ordine di `recorded_at`.
- **Riga:** ora locale, titolo o "senza titolo", durata, dispositivo (nome leggibile),
  dimensione, badge "data stimata". Espansa: player, metadati (firmware, batteria,
  alimentazione, sha256, ricezione) e l'elenco di tutti i file con lo stesso nome base.
- **Azioni:** modifica il titolo (in linea), scarica, **correggi data/ora** (aggiorna
  `recorded_at`, `date_estimated = false`; se cambia il giorno sposta WAV, sidecar e
  derivati nella nuova cartella con lo stesso nome, suffisso solo se occupato), sposta
  nel cestino.
- **Filtro dispositivo** nella query string.
- **Da sistemare:** elenco delle registrazioni con `date_estimated = true`.
- **Cestino:** elenco, ripristino nella posizione originale, eliminazione definitiva;
  pulizia automatica dopo `TRASH_RETENTION_DAYS` (all'avvio e una volta al giorno).
- **Dispositivi:** nome leggibile modificabile, id, tipo, ultimo contatto, firmware,
  batteria e alimentazione dell'ultimo upload, versione firmware pubblicata per quel
  tipo. Creazione del token solo da CLI (il token è mostrato una volta sola).
- **Audio** servito con supporto `Range`.
- **Login:** utente singolo, password impostata da CLI (hash argon2), cookie di sessione
  `HttpOnly`, `Secure` (dietro HTTPS), `SameSite=Lax`, token CSRF sulle azioni. Dopo 5
  tentativi falliti in 15 minuti dallo stesso client (Cf-Connecting-Ip, altrimenti IP),
  blocco di quel client; oltre 100 falliti in 15 minuti da tutti i client, blocco
  generale. `set-password` e `secondbrain unlock` azzerano i blocchi. Il login è
  serializzato (un solo controllo password alla volta).

## 8. Schema Postgres

Migrazioni con Alembic.

- `devices`: `id` (MAC, testo), `name`, `type` (es. `epaper154`), `token_hash`,
  `created_at`, `last_seen_at`, `last_firmware`, `last_battery_pct`, `last_battery_v`,
  `last_power_source`.
- `captures`: `id` (uuid), `device_id` → `devices`, `capture_id`, `recorded_at` (UTC),
  `date_estimated`, `received_at`, `day` (data della cartella, per i conteggi dell'albero;
  resta quella originale anche nel cestino), `rel_path` (del WAV, unico), `title`, `duration_s`,
  `size_bytes`, `sha256`, `firmware_version`, `battery_pct`, `battery_v`,
  `power_source`, `trashed_at`. Indici su `(device_id, capture_id)` (non unico, vedi §6
  punto 5), `recorded_at` e `day`. `rel_path` e `day` non stanno nel sidecar: si
  ricavano dalla posizione del file, così `rescan` segue anche gli spostamenti a mano.
- `firmware_releases`: `type`, `version`, `file`, `sha256`, `published_at`, `current`
  (una sola corrente per tipo).
- `users` (una riga), `web_sessions` (id casuale, token CSRF, scadenza 30 giorni),
  `login_attempts`.

## 9. OTA

- Binari nel volume `firmware/<tipo>/secondbrain-X.Y.Z.bin`.
- `GET /firmware/<tipo>/manifest.json` e l'alias `GET /firmware/manifest.json` (tipo
  `epaper154`, il percorso del firmware attuale). Il manifest `{version, url, sha256}`
  viene generato dalla release corrente, con l'URL costruito dall'header `Host` e
  dallo schema della richiesta (`X-Forwarded-Proto` dietro il tunnel).
  Nessuna release → `404`: il firmware attuale logga l'errore e prosegue senza
  aggiornare, come per ogni risposta diversa da `200`.
- `GET /firmware/<tipo>/<file>.bin` serve il binario.
- `secondbrain firmware publish <file.bin> --type epaper154 --version X.Y.Z` copia il
  binario, calcola lo sha256 e lo rende corrente; `firmware rollback --type ...` torna
  alla release precedente. Pubblicare è sempre esplicito: sparisce la trappola del
  manifest dimenticato con una versione più alta.

## 10. Test e verifica

- **Unit (pure):** `naming` (fusi, cambio dell'ora legale, mezzanotte, timestamp non
  plausibili, collisioni), `wav`, confronto versioni.
- **`archive`** su directory temporanee: scrittura atomica, sidecar, sposta con derivati,
  cestino, ripristino, pulizia di `.incoming`.
- **Integrazione** su Postgres vero (container dedicato ai test) con il `TestClient` di
  FastAPI: tutti i casi di §6 (201, 409, stesso id con contenuto diverso, 400, 401, 403,
  413, 422, data stimata), `rescan` dopo aver svuotato il DB, correggi data, cestino,
  manifest OTA con e senza release.
- **UI:** test HTTP su login, blocco dopo tentativi falliti, CSRF, azioni principali.
- Test prima dell'implementazione, come per il firmware.
- **Verifica end-to-end sul Mac:** `docker compose up` con
  `ALLOW_UNAUTHENTICATED_LAN=true`, device vero puntato al Mac al posto di
  `capture_server.py`: registrazioni reali in archivio, `409` sui ritentativi, OTA pull
  di una build di test pubblicata dalla CLI, navigazione e ascolto dalla UI.
- **Verifica sulla ZimaBoard:** stesso compose, profilo `tunnel`, due hostname (UI e
  dispositivi), login da telefono su rete mobile. `ALLOW_UNAUTHENTICATED_LAN=true`: il
  device vero carica sulla ZimaBoard dalla LAN di casa senza token; dal tunnel senza
  token → `401`, con token (via `curl` e un WAV vero, finché il firmware non supporta
  HTTPS e token, §11) → `201`.
- **Backup:** documentato nel README (snapshot o rsync di `archive/`, `pg_dump`), non
  automatizzato.

## 11. Da portare nella spec firmware "HTTPS + token" (separata)

- Client HTTP in TLS con il bundle di certificati di ESP-IDF; header `Authorization:
  Bearer` con il token in `secrets.h`.
- `401`, `403` e `429` diventano "tieni il file e interrompi il sync", come i 5xx. Oggi
  ogni 4xx sposta il file in `rejected/`: con i token un errore di configurazione
  manderebbe in `rejected/` l'intera coda. Va fatto prima di attivare i token.
- Con un URL pubblico unico, il server per rete in `WIFI_NETWORKS` diventa opzionale.
- Quando tutti i dispositivi mandano il token, `ALLOW_UNAUTHENTICATED_LAN` torna a
  `false` sulla ZimaBoard.
- Hostname dei dispositivi dedicato nel tunnel, con Bot Fight Mode/WAF disattivati;
  opzionale un service token di Cloudflare Access (header `CF-Access-Client-Id` e
  `CF-Access-Client-Secret`) come seconda barriera.
- Limite del piano gratuito di Cloudflare: 100 MB per richiesta (una cattura da 10 min
  è ~19 MB).
