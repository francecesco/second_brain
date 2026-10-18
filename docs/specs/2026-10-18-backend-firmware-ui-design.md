# Backend — Pubblicazione del firmware dalla pagina Dispositivi — Design / Spec

**Data:** 2026-10-18
**Stato:** implementata sul branch `backend-firmware-ui` il 2026-10-18; 756 test del backend
verdi, screenshot a 1280 e 390 px (chiaro e scuro). Dopo la prima prova dell'autore in locale
"Torna alla precedente" è diventato "Rendi corrente" per riga (vedi §2.2); 757 test.
Deviazioni: gli errori arrivano con l'iniziale maiuscola; "corrente" sta nella cella della
versione. Da provare di nuovo in locale, poi deploy sulla ZimaBoard su decisione dell'autore.

## 1. Obiettivo

Oggi una nuova versione del firmware arriva al device in due modi: via cavo con `esptool`
(serve il cavo dati e il device sveglio) oppure pubblicando una release sulla ZimaBoard da
riga di comando (`scp` del binario, `docker compose cp`, `secondbrain firmware publish`).
L'autore vuole che **l'OTA dalla ZimaBoard sia la strada normale** e che si faccia dalla UI:
nella pagina Dispositivi si carica il `.bin` compilato sul Mac e il device si aggiorna al
prossimo risveglio. Il cavo resta per il recupero.

Riuscita: compilata la release sul Mac, dal browser si sceglie il file, si preme "Pubblica"
e dopo al massimo 5 minuti la riga del device mostra la nuova versione.

## 2. Decisioni (approvate in chat il 2026-10-18)

1. **Versione letta dal binario**, non digitata. Il binario ESP-IDF porta in testa
   l'`esp_app_desc_t` (a offset 32: magic `0xABCD5432`, versione a offset 48 su 32 byte,
   nome del progetto a offset 80 su 32 byte). Il backend accetta solo file con il magic
   giusto e progetto `secondbrain_fw`; la versione deve essere `X.Y.Z` e non già pubblicata.
2. Una **card "Firmware" nella pagina Dispositivi**, una per tipo di dispositivo presente:
   release corrente, elenco delle precedenti, campo file + "Pubblica", "Rendi corrente" su
   ogni release non corrente (con conferma), avviso che il device si aggiorna al prossimo
   risveglio. Il primo giro aveva "Torna alla precedente" (il rollback della CLI): provandolo
   l'autore è tornato indietro senza poter tornare avanti, perché ripubblicare una versione
   già in elenco è rifiutato; il pulsante per riga copre le due direzioni.
3. **Il flusso sul Mac** è uno script: `firmware/tools/release.sh X.Y.Z` scrive
   `version.txt`, compila, copia il binario versionato in `~/Downloads/`, rimette
   `version.txt` a `0.1.0`. Scritto in `CLAUDE.md` come procedura standard.
4. La CLI resta: `firmware publish` con `--version` opzionale (se manca, dal binario).

## 3. Interazione

- `POST /devices/firmware/{type}` (multipart, campo `file`, `csrf`): legge l'intestazione,
  pubblica come release corrente, redirect `303` a `/devices?pubblicata=X.Y.Z#firmware`.
  Errori (file non firmware, progetto diverso, versione non valida o già pubblicata, file
  vuoto, oltre `max_upload_bytes`): redirect a `/devices?errore=<testo>#firmware`, la pagina
  mostra un `.notice` con `role="alert"`.
- `POST /devices/firmware/{type}/current` (`csrf`, `version`): rende corrente una release in
  elenco (`ota.set_current`), redirect a `/devices?corrente=X.Y.Z#firmware`; errore come
  sopra. `secondbrain firmware rollback` resta nella CLI.
- La card segue `docs/stile-ui.md`: `.card` con `h2` "Firmware <tipo>", tabella delle
  release (versione, data, sha corto, "corrente"), form `.actions` con `<input type=file
  accept=".bin">` e pulsante pieno "Pubblica"; "Rendi corrente" pulsante normale nella riga
  della release, con `onsubmit` di conferma.
- Solo utente loggato (come il resto della UI) e CSRF. Il file passa in memoria (1,2 MB,
  limite `max_upload_bytes` già usato per i WAV) e va su disco con la `publish` esistente.

## 4. Non obiettivi

- Push attivo verso il device (`POST /ota` del device in DEV MODE): il device dorme, il
  pull al risveglio è l'unico canale affidabile.
- Cancellare release vecchie dalla UI.
- Compilare sul server.
