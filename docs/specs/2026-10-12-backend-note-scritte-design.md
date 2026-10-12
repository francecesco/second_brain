# Backend — Note scritte dall'app — Design / Spec

**Data:** 2026-10-12
**Stato:** implementata sul branch `backend-note-scritte` il 2026-10-12; 597 test del
backend verdi, screenshot a 1280 e 390 px (chiaro e scuro) fatti. Provata in locale
dall'autore: **non ancora**. Deploy sulla ZimaBoard: **dopo l'ok dell'autore**. Deviazioni
emerse: creazione serializzata con un advisory lock di Postgres (due doppi invii
simultanei facevano cancellare al perdente i file del vincitore); successo con redirect a
`/todo#salvata` e bozza del browser (`sb-draft`) tolta solo su quel segno (una sessione
scaduta non la perde); su `/new` il pulsante dell'intestazione è nello stato `on`, non
pieno, per lasciare "Salva" come unico pieno; errore imprevisto o del disco: 500 con il
testo mantenuto; "Annulla" come link `a.btn`; rescan di una nota scritta senza `.md` la
rimette in coda e il worker ricostruisce la trascrizione dal `.txt` senza provider audio.

## 1. Obiettivo

Quando il device non c'è o non si può parlare, l'autore vuole scrivere velocemente una nota
dall'app web (soprattutto dal telefono). Una volta salvata, la nota segue la stessa strada di
una registrazione trascritta: titolo, riassunto e tag dall'AI (regole dei nomi, vocabolario,
tag esistenti), archivio, "Da fare", ricerca, tag, vault Obsidian, spunta e cestino.

Riuscita: dal telefono, in pochi secondi la nota è salvata; dopo un minuto ha titolo e tag.

## 2. Decisioni (approvate in chat il 2026-10-12)

1. Il testo scritto **è la trascrizione** e l'AI non lo cambia; l'AI fa solo l'arricchimento.
2. Pulsante **"Nuova nota"** sempre visibile nell'intestazione, che apre la pagina `/new`.
3. **Il testo prende il posto del WAV nell'archivio** (approccio A): `<base>.txt` accanto al
   sidecar, i file restano la verità e `rescan` ricostruisce tutto. Scartati: nota solo nel
   vault (B, il testo originale vivrebbe solo in un file modificabile e `rescan` non lo
   vedrebbe) e WAV finto (C).

## 3. Pagina e interazione

- **Pulsante "Nuova nota"** nell'intestazione, dopo la ricerca e prima del menu: icona
  "matita", accento arancione (è l'azione principale, `docs/stile-ui.md`); a 390 px solo
  l'icona, su desktop icona ed etichetta.
- **Pagina `/new`**: una `textarea` grande con `autofocus`, pulsante "Salva" (anche
  ⌘/Ctrl+Invio), link "Annulla". Funziona senza JavaScript (form POST classico). Nessun altro
  campo: titolo e tag li fa l'AI, si correggono dopo dal dettaglio.
- **Dopo il salvataggio**: redirect 303 a `/todo`, dove la nota è in cima con il LED "in
  elaborazione".
- **Limiti**: testo vuoto o solo spazi → errore sotto il campo; massimo **20.000 caratteri**
  (`MAX_TEXT_NOTE_CHARS`), circa 10 minuti di parlato. In errore la pagina torna con il testo
  nel campo (422).
- **Bozza**: il testo non salvato si tiene in `localStorage` (con try/catch, è solo una
  comodità) e si cancella al salvataggio riuscito.
- **Doppio invio**: la pagina genera un `capture_id` nel formato del device
  (`cap_AAAAMMGG_HHMMSS`, istante UTC di apertura della pagina) in un campo nascosto; vale la
  logica dei doppioni di `POST /captures`: stesso id e stesso `sha256` → nessuna nota nuova,
  redirect come se fosse andata a buon fine.

## 4. Dati e archivio

- **Dispositivo "App"**: riga in `devices` con tipo `web` (`TEXT_DEVICE_TYPE`), nome "App",
  id esadecimale di 12 caratteri generato, creata al primo salvataggio e ritrovata per tipo.
  Senza token: da `POST /captures` non si carica niente a suo nome. Nella pagina Dispositivi
  si rinomina ma non c'è "Rigenera token".
- **File**: nella cartella del giorno `<base>.txt` (originale UTF-8, immutabile, testo come
  scritto con i fine riga normalizzati a `\n`) e `<base>.json`, il sidecar con i campi di
  oggi: `duration_s = 0`, `size_bytes` e `sha256` del `.txt`, `recorded_at` = istante del
  salvataggio, `date_estimated = false`, campi del device (`firmware_version`, batteria)
  vuoti. `<base>` come per le registrazioni (`naming.base_name`).
- **Tipo della nota**: dall'estensione di `rel_path` (`.txt` scritta, `.wav` vocale). Niente
  colonna né campo del sidecar nuovi, niente migrazione.
- **`archive.py`**: i punti che assumono `.wav` diventano "sorgente" `.wav` o `.txt`:
  `commit_capture` riceve l'estensione; `move` conserva l'estensione (oggi restituisce sempre
  `.wav`); `iter_sidecar_rels` restituisce la sorgente che esiste (se nessuna esiste, il
  `.wav` come oggi, per il messaggio "sidecar senza sorgente"); "WAV senza sidecar" diventa
  "sorgente senza sidecar". Cestino, ripristino, correzione della data e cancellazione
  lavorano già sul nome base.
- **Nota e lavoro**, nella stessa transazione della riga del catalogo: nota `.md` nel vault con
  `transcript` = testo, `edited: ["transcript"]`, lingua dalle impostazioni, provider di
  trascrizione vuoto; lavoro accodato in fase `enrich`. Se il commit fallisce, i file si
  tolgono con la stessa regola dell'upload (`_cleanup_after_commit_failure`).
- **`rescan`**: una nota `.txt` senza `.md` va in coda come ogni nota senza `.md`; il worker
  (rete di sicurezza, §5) ricostruisce la trascrizione dal `.txt` e la arricchisce. Un solo
  percorso di codice. Se il `.md` c'è vince lui, perché contiene le correzioni.
- **UI**: `/captures/{id}/audio` di una nota scritta → 404; niente player nel dettaglio né
  "Ascolta" nel cestino; nella riga "App · N parole" al posto di durata e dimensione.

## 5. Pipeline

- La nota scritta nasce in fase `enrich`; il worker la arricchisce come una trascrizione
  (regole dei nomi, vocabolario, tag noti).
- **Rete di sicurezza**: se una nota `.txt` arriva in fase `transcribe`, il worker non chiama
  i provider audio: legge il `.txt`, lo applica come trascrizione corretta a mano e passa a
  `enrich`.
- "Rielabora" rifà l'arricchimento senza toccare il testo (`edited` contiene `transcript`);
  `retag`, correzioni a mano e modifiche da Obsidian funzionano come oggi.
- **Titolo provvisorio**: finché l'AI non ha dato un titolo, una nota scritta mostra l'inizio
  della prima riga non vuota del testo (al massimo 60 caratteri, taglio su parola con "…")
  invece di "Nota del pomeriggio". Come il titolo di default, è solo visualizzazione e nome
  del file nel vault, mai salvato come titolo.

## 6. Errori

| Caso | Comportamento |
|---|---|
| Provider AI giù o senza chiavi | Nota salvata e visibile con testo e titolo provvisorio; il lavoro ritenta col backoff (1 min → 6 h); avviso "chiavi mancanti" come oggi |
| Testo vuoto o oltre 20.000 caratteri | 422, pagina con errore e testo nel campo |
| CSRF errato o sessione scaduta | Come le altre azioni: 403 / login |
| Disco pieno o errore di scrittura | Pagina con errore e testo nel campo; file parziali tolti |
| Doppio invio | Redirect come per un salvataggio riuscito, nessuna nota nuova |

## 7. Modifiche

- `archive.py` (sorgente `.wav`/`.txt`), `rescan.py`, `naming.py` (costanti), nuovo modulo
  `textnotes.py` (device "App", creazione della nota, titolo provvisorio), `notes.py`
  (`effective_title` col titolo provvisorio), `worker.py` (rete di sicurezza), `devices`
  (niente rigenerazione per `web`).
- Web: nuovo `web/compose.py` (GET/POST `/new`), audio 404 per `.txt`, template `new.html`,
  `finder.html` (pulsante), `_row.html`, `_detail.html`, `trash.html`, `devices.html`,
  `_icons.html` (matita), `style.css`.
- Documentazione: `README.md` del backend, `docs/stile-ui.md` (pulsante d'azione
  nell'intestazione), `CLAUDE.md`.

## 8. Verifica

1. Test del backend (TDD): archivio con sorgenti miste e `move`/cestino che conservano
   l'estensione; creazione (device creato una volta, file, sidecar, catalogo, nota nel vault,
   `edited`, fase `enrich`, doppione, testo vuoto o troppo lungo); worker (arricchimento
   diretto, rete di sicurezza senza provider audio); `rescan` con e senza `.md`; web (`/new`,
   CSRF, redirect, riga senza player e con "N parole", audio 404, titolo provvisorio).
2. Screenshot a 1280 e 390 px, chiaro e scuro: intestazione col pulsante, `/new`, riga e
   dettaglio di una nota scritta, controllati contro `docs/stile-ui.md`.
3. In locale sul Mac con le chiavi vere: prova dell'autore dal PC e dal telefono in LAN.
4. Deploy sulla ZimaBoard solo dopo l'ok dell'autore.
