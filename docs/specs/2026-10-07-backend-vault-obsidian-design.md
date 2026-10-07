# Backend — Note nel vault Obsidian — Design / Spec

**Data:** 2026-10-07
**Stato:** implementato e **verificato sul Mac il 2026-10-07** (branch `backend-vault`, piano
`docs/plans/2026-10-07-backend-vault-obsidian.md`, 524 test). `vault migrate` ha spostato le 13
note esistenti; sullo stack vero: nota scritta dal worker in `Captures/2026/<data> <ora>
<titolo>.md` con `id`, `aliases`, tag; modifica di corpo e tag sul file ripresa dal worker in
45 s (`edited`), rinomina del file → titolo manuale, correzione dalla UI riscritta nel file,
cestino → nota fuori dal vault accanto al WAV, ripristino → di nuovo nel vault, `rescan`
senza problemi. **Da fare**: la prova con Obsidian vero (l'autore lo installa l'8 ottobre:
le modifiche sono state simulate scrivendo i file, come fa Obsidian) e Syncthing al deploy.
Deviazioni: `vault`, `tz` e `public_url` sono parametri a chiave di `write_note` e delle
funzioni di `library` (senza `vault` la nota resta accanto al WAV: lo usano solo i test
vecchi); `title`/`aliases` nel frontmatter solo se la nota ha un titolo manuale o automatico
(il titolo di default per fascia sta solo nel nome del file, altrimenti `rescan` lo
rileggerebbe come automatico); un file rinominato in Obsidian vince sul `title` del
frontmatter; lo stato del vault in Impostazioni è una sezione (note, ultimo controllo,
problemi). Dalla revisione finale: il nome con suffisso di collisione `(k)` resta stabile
tra le scritture; prima di sovrascrivere una nota il backend fonde le modifiche del file non
ancora viste (confrontandole con quanto aveva scritto l'ultima volta) e non riscrive un file
identico; il titolo dal nome del file si prende con un'espressione regolare (senza prefisso
vale tutto il nome, troncato a 200); il titolo impostato da Obsidian finisce anche nel sidecar
e il frontmatter tiene `title_auto` distinto, così `rescan` conserva entrambi; un file che
fa fallire la ripresa non ferma gli altri; il ripristino scrive nel vault prima di togliere la
copia dal cestino; `rescan` si rifiuta di girare se il catalogo ha note nel vault ma la
cartella `Captures/` manca; `vault_sync` rilegge il file sotto il lock; `trash purge` da CLI
passa il vault.

## 1. Obiettivo

Le note delle registrazioni (trascrizione, titolo, riassunto, tag) devono vivere in un
vault Obsidian, cioè una cartella di file Markdown che Syncthing porta su telefono e
portatile. Una sola copia della nota: il backend la scrive lì, Obsidian la legge e la
modifica, il backend riprende le modifiche. La UI web non cambia nell'uso: trascrizione,
riassunto e tag restano visibili e correggibili, e il file della nota resta elencato e
scaricabile dal dettaglio della registrazione (requisito dell'autore).

Realizza il §7 della spec di progetto (`2026-09-14-second-brain-design.md`) con il backend
di oggi: Postgres come catalogo ricostruibile, archivio dei WAV separato dal vault.

## 2. Contesto e vincoli

- Oggi il `.md` sta in `archive/AAAA/MM/GG/<base>.md` accanto a WAV, sidecar e `.ai.json`
  (spec AI §6); è la verità dei campi AI e `rescan` lo rilegge. `notes.write_note` scrive
  file e catalogo insieme sotto il lock di riga; `edited` protegge le correzioni a mano.
- Cestino, ripristino e correzione della data spostano tutti i file `<base>.*` della
  cartella del giorno (`archive.move`).
- Obsidian: il nome del file è il nome della nota; il frontmatter YAML è visibile come
  proprietà; `tags` e `aliases` nel frontmatter alimentano il pannello dei tag e la ricerca.
- Syncthing scrive i file in arrivo come temporanei (`.syncthing.<nome>.tmp`) e li
  rinomina alla fine; può produrre file `<nome>.sync-conflict-...md` in caso di conflitto.
- Il backend gira in Docker: il vault è un volume (`VAULT_DIR`), sulla ZimaBoard la
  cartella condivisa da Syncthing.
- Non c'è un segnale di modifica affidabile dentro un volume Docker (inotify incostante):
  si usa la data di modifica dei file.

## 3. Decisioni (approvate in chat)

1. **Una sola copia della nota, nel vault.** Il backend scrive il `.md` in
   `<vault>/Captures/<anno>/`; l'archivio tiene WAV, sidecar e `.ai.json`. Niente copia
   dell'audio nel vault.
2. **Nome con data, ora e titolo**: `2026-10-07 11.58 Promemoria per annaffiare le
   piante.md`. Quando il titolo cambia il backend rinomina il file. La chiave che lega file
   e registrazione è `id` nel frontmatter, non il nome.
3. **Modifiche da Obsidian riprese dal worker**, ogni minuto, confrontando la data di
   modifica dei file con quella registrata; i campi cambiati vengono marcati in `edited`.
4. **UI web invariata nell'uso**: campi AI visibili e correggibili come oggi; il `.md`
   resta elencato nel dettaglio con il suo percorso nel vault, scaricabile.

## 4. Cartelle e configurazione

- `VAULT_DIR` (compose: `${DATA_DIR}/vault:/data/vault`, default `./data/vault`). Il
  backend crea `Captures/` se manca e non tocca nient'altro nel vault (`.obsidian/`, note
  dell'utente, `Daily/`).
- `PUBLIC_URL` (opzionale, es. `https://brain.<dominio>`): base del link alla UI nel
  frontmatter. Vuoto: nessun `url`.
- Il catalogo acquista `note_path` (percorso del `.md` relativo al vault, `NULL` se la
  nota non è mai stata scritta o è nel cestino) e `note_mtime` (data di modifica del file
  dopo l'ultima scrittura o lettura del backend, in secondi con frazione).

## 5. Nome e formato della nota

**Nome**: `<AAAA-MM-GG> <HH.MM> <titolo>.md` in `Captures/<AAAA>/`, con ora locale di
registrazione (`tz_archive`) e titolo = quello mostrato dalla UI (`title` manuale, poi
`title_auto`, poi il titolo di default per fascia del giorno). Il titolo nel nome è
ripulito: caratteri vietati (`/ \ : * ? " < > |` e controlli) sostituiti da spazio,
spazi compressi, troncato a 80 caratteri, senza punto finale. Collisione nella stessa
cartella → suffisso ` (2)`, ` (3)`. Funzione pura `vault_note_name(recorded_local, title)`.

**Frontmatter** (ordine fisso):

```yaml
---
id: cap_20261007_095829          # chiave: capture_id del device
device: 70041dd8263c
created: '2026-10-07T11:58:29+02:00'
duration: 6                      # secondi, intero
title: Promemoria per annaffiare le piante
aliases: [Promemoria per annaffiare le piante]
summary: Ricordarsi di annaffiare le piante questa sera alle 20:30.
tags: [piante, promemoria, giardinaggio]
url: https://brain.esempio.it/captures/<uuid>   # solo con PUBLIC_URL
language: it
provider: gemini
models: {transcribe: gemini-3.8-flash, enrich: gemini-3.8-flash}
processed_at: '2026-10-07T10:49:37+02:00'
edited: []
---
Testo della trascrizione.
```

`id`, `device`, `created`, `duration`, `url` sono scritti dal backend e ignorati in
lettura (non sono campi modificabili). Da Obsidian si modificano `title`, `summary`,
`tags` e il corpo; `aliases` segue `title` e non viene letto. Il parser di oggi
(`parse_note`) resta valido: i campi in più vengono ignorati.

## 6. Scrittura, rinomina, cestino

- `notes.write_note` scrive la nota nel vault invece che nell'archivio: calcola il nome
  atteso, se `note_path` esiste ed è diverso rinomina (`os.replace`, stesso filesystem),
  scrive il contenuto in modo atomico (temporaneo + `os.replace` nella stessa cartella),
  aggiorna `note_path` e `note_mtime`. Sotto il lock di riga, come oggi.
- `set_title` (titolo manuale dalla UI) e `correct_recorded_at` (cambia data/ora, quindi
  il nome) chiamano la stessa scrittura: il file viene rinominato e il frontmatter
  aggiornato.
- **Cestino**: il `.md` viene spostato nel cestino dell'archivio accanto al WAV
  (`archive/.trash/AAAA/MM/GG/<base>.md`) e `note_path` diventa `NULL`; così sparisce dal
  vault e da Obsidian. **Ripristino**: il file torna nel vault con il nome atteso.
  **Eliminazione definitiva** e `trash purge` cancellano anche quel `.md`.
- Il file `.ai.json` resta nell'archivio.

## 7. Modifiche da Obsidian (`vault_sync`)

Il worker, quando non ha lavori (`run_once` → `False`) e almeno `VAULT_CHECK_INTERVAL_S`
(60 s) dopo l'ultimo controllo, esegue `vault_sync.check`:

1. Elenca i file `*.md` sotto `Captures/`, saltando `.syncthing.*`, `*.tmp`, `*~` e i
   `*.sync-conflict-*` (segnalati nel log una volta).
2. Per ogni file con data di modifica diversa da `note_mtime` del catalogo (cercato per
   `note_path`): lo legge, estrae `id` dal frontmatter, ricollega per `capture_id` se il
   percorso non corrisponde (file rinominato in Obsidian: il nuovo nome vale come titolo
   manuale, se la parte titolo differisce da quella attesa). Sotto il lock di riga
   confronta `title`, `summary`, `tags`, corpo con il catalogo: ogni campo diverso viene
   applicato come correzione a mano (`apply_edit`, quindi finisce in `edited`); il titolo
   diverso diventa `title` manuale. Poi riscrive la nota (frontmatter normalizzato, nome
   atteso) e aggiorna `note_mtime`.
3. File senza `id`, con `id` sconosciuto o con frontmatter illeggibile: segnalati nel log
   (una volta per file finché non cambia) e lasciati stare.
4. Nota a catalogo con `note_path` il cui file è sparito: segnalata; alla prossima
   scrittura del backend il file viene ricreato.

Un lavoro di elaborazione in corso sulla stessa nota è protetto dal lock di riga e da
`edited` (il worker rilegge `edited` prima di scrivere, spec AI §10).

## 8. UI web

- Dettaglio: l'elenco "File" mostra i file dell'archivio come oggi più la nota con il
  percorso nel vault (`Captures/2026/2026-10-07 11.58 ….md`), scaricabile da
  `GET /captures/{id}/note`. Nel cestino la nota non è elencata.
- Campi AI, "Rielabora", ricerca, vista per tag: invariati (leggono il catalogo).
- Pagina Impostazioni: riga informativa "Vault: `<VAULT_DIR>`, N note, ultimo controllo
  alle HH:MM" e il numero di file segnalati dall'ultimo controllo.

## 9. Migrazione e rescan

- `secondbrain vault migrate`: per ogni registrazione con un `<base>.md` nell'archivio
  (fuori dal cestino) scrive la nota nel vault con il nome atteso e cancella il `.md`
  dall'archivio; idempotente; stampa quante note ha spostato. Le note nel cestino restano
  come sono (il loro `.md` è già nel cestino dell'archivio).
- `rescan`: ricostruisce il catalogo dall'archivio come oggi, poi ricollega le note del
  vault per `id` (`note_path`, campi AI dal file); una nota fuori dal cestino senza file nel
  vault va in coda come oggi; un `.md` ancora nell'archivio viene letto e segnalato come
  "da migrare".
- Migrazione Alembic: colonne `note_path` (unique, nullable) e `note_mtime`.

## 10. Verifica

**Test**: nome del file (pulizia, troncamento, collisioni), frontmatter (campi, ordine,
`aliases`, `url` con e senza `PUBLIC_URL`), rinomina al cambio di titolo e di data,
cestino/ripristino/eliminazione, `vault_sync` (file modificato → `edited`; rinominato →
titolo manuale; temporanei e conflitti ignorati; `id` sconosciuto segnalato; file sparito),
migrazione idempotente, rescan con vault.

**Sul Mac**: Obsidian aperto su `backend/data/vault`; registrazione dal device → la nota
compare in Obsidian con titolo e tag; modifica del corpo e di un tag in Obsidian → in UI
entro un minuto, con `edited`; correzione dalla UI → file aggiornato in Obsidian;
rinomina in Obsidian → titolo manuale in UI; cestino → sparisce da Obsidian, ripristino →
torna; `vault migrate` sulle note esistenti; `rescan` dopo la migrazione. Syncthing non fa
parte della verifica.

## 11. Non-goals

- Daily notes, PiAgent, wikilink automatici, note create in Obsidian non legate a
  registrazioni, copia dell'audio nel vault, risoluzione dei conflitti di Syncthing.
- Modifica da Obsidian dei campi tecnici (`created`, `device`, `provider`, ...).

## 12. Rischi

- **Rinomina e link**: un `[[link]]` scritto a mano verso una nota si rompe se il
  backend la rinomina (titolo cambiato). Accettato (scelta 2); `aliases` aiuta la ricerca.
- **Conflitti Syncthing**: una modifica in Obsidian e una scrittura del backend sullo
  stesso file nello stesso minuto possono produrre un `sync-conflict`; il backend lo
  segnala e non lo tocca. Ridotto scrivendo il file solo quando i campi cambiano.
- **Data di modifica**: Syncthing conserva l'mtime del mittente; il confronto è per
  disuguaglianza, non per "più recente".
- **Volume Docker su macOS**: l'mtime dei file scritti dall'host arriva al container con
  la stessa precisione; verificato nella prova sul Mac.
