# Backend — Elaborazione AI delle note (trascrizione, titolo, riassunto, tag) — Design / Spec

**Data:** 2026-09-29
**Stato:** implementato e **verificato col device vero sul Mac il 2026-09-30** (branch
`backend-elaborazione-ai`, piano `docs/plans/2026-09-29-backend-elaborazione-ai.md`, 15 task,
494 test). Verificati con chiavi vere: Groq e Gemini (trascrizione, titolo, riassunto, tag),
cambio del provider principale, arretrato delle note già in archivio, utilizzo mensile,
nessuna chiave nei log. **Verificati il 2026-10-07** sul Mac col backend vero e le
chiavi di Groq e Gemini: fallback con una chiave sbagliata sul principale; arresto del worker
durante una trascrizione (SIGTERM: lavoro di nuovo in coda senza contare il tentativo;
SIGKILL: ripreso allo scadere del lease con `attempts=1`); pausa; correzione + Rielabora
(campi corretti conservati, trascrizione saltata); cestino durante l'elaborazione (risultato
scartato; il ripristino non rimette in coda, ci pensa `rescan`); rescan dopo una modifica a
mano del `.md` (e con un `.md` rotto: segnalato, catalogo intatto). La ricerca "dal
telefono" è stata verificata a 390 px con Chrome headless, non su un telefono vero.
Deviazioni emerse in esecuzione: il `.ai.json` si compone nelle due fasi di una stessa
elaborazione (l'arricchimento aggiunge la sua parte), e "Rielabora" lo sostituisce; la
ricerca usa la lingua delle impostazioni, mentre ogni nota è indicizzata nella propria;
`processed_at` è tra virgolette nel YAML; riassunto e tag si correggono solo su una nota già
elaborata (la trascrizione sempre); `enrich_provider` nel frontmatter solo quando è diverso
da `provider`; una `SETTINGS_KEY` malformata equivale a una assente (errore nel log, avviso
nella pagina, il resto del backend funziona); un file audio sparito durante l'elaborazione
si riprova con il backoff invece di fallire subito; avviso quando nessun provider ha un
modello di testo; una risposta di Gemini senza parti di testo (`content: {}` con
`finishReason: STOP`, vista il 2026-10-07 su un WAV già trascritto due volte) è un errore
del servizio e passa al provider successivo, mentre il silenzio vero arriva come parte con
testo vuoto; tutte le operazioni di `library` prendono il lock sulla riga della nota.
**Dipende da:** `docs/specs/2026-09-28-backend-archivio-design.md` (archivio su disco,
catalogo Postgres, UI finder), implementata e verificata col device.
**Cambia rispetto alla spec di progetto** (`docs/specs/2026-09-14-second-brain-design.md`):
la trascrizione non è più locale con faster-whisper ma affidata a provider esterni (§2).

---

## 1. Obiettivo

Ogni nota archiviata viene elaborata in automatico: **trascrizione**, **titolo**,
**riassunto breve** e **tag**. Il risultato si legge e si corregge nel finder e si trova
con una **ricerca full-text**. I provider sono intercambiabili e si configurano da una
pagina di impostazioni con le proprie chiavi API: oggi Gemini e Groq, poi OpenAI, in
futuro un modello locale.

Criterio di successo: registri una nota col device e dopo qualche decina di secondi la
trovi nel finder con un titolo sensato, un testo che si capisce senza ascoltare l'audio,
riassunto e tag; la ricerca per parola la trova.

## 2. Perché provider esterni

Misurato sulla ZimaBoard il 2026-09-29: ZimaBoard 832, Celeron N3450 4 core 1,1 GHz
**senza AVX/AVX2**, 7,6 GB di RAM di cui ~4 GB liberi e condivisi con Immich e altri
servizi, GPU HD 500 non utilizzabile. faster-whisper senza AVX va più lento del tempo
reale anche con modelli piccoli, e i modelli buoni per l'italiano non ci stanno. Si
accetta quindi che l'audio esca di casa verso il provider scelto; la scelta si potrà
rivedere passando a un NAS più potente, aggiungendo un adattatore `local` (§4).

## 3. Decisioni prese con l'autore

| Tema | Scelta |
|---|---|
| Provider | infrastruttura generica, più chiavi API impostate dalla UI; prima Gemini e Groq, poi OpenAI |
| Più provider configurati | **uno principale + riserve in ordine**; una sola elaborazione per nota |
| Cosa si produce | trascrizione, titolo, riassunto breve, tag |
| Quando | **automatica all'arrivo**, con interruttore di pausa; arretrato elaborato su richiesta |
| Lingua | impostazione, default italiano; riassunto e tag nella stessa lingua |
| Uso nel finder | dettaglio + **ricerca full-text** + clic sul tag |
| Correzioni | testo, riassunto e tag **modificabili**; i campi corretti a mano non vengono più sovrascritti |
| Esecuzione | **worker separato** con la stessa immagine, coda in Postgres |

## 4. Architettura

```
device ──POST /captures──► app ──► archivio + catalogo + job (stessa transazione)
                                               │
                                   worker ◄────┘  (coda in Postgres)
                                     │
                     ┌───────────────┴───────────────┐
                 Transcriber                       Enricher
          (audio → testo)            (testo → titolo, riassunto, tag)
                     └──── adattatore del provider ───┘
                         openai_compatible │ gemini │ (local, futuro)
```

- **Servizio `worker`** nel compose: stessa immagine dell'app, comando
  `secondbrain worker`, stesse variabili d'ambiente e stesso volume dell'archivio.
  Elabora un lavoro alla volta; un crash o una chiamata lenta non toccano UI e ingest.
- **Due interfacce** in `secondbrain/ai/`:
  - `Transcriber.transcribe(audio_path, language) -> Transcript` (testo + risposta
    grezza);
  - `Enricher.enrich(text, language) -> Enrichment` (titolo, riassunto, tag + risposta
    grezza), chiesti al modello come JSON con schema fisso e validati (§9).
- **Un provider implementa entrambe** con la stessa chiave. Adattatori:
  - `openai_compatible`: Groq e OpenAI (stesse API `/audio/transcriptions` e
    `/chat/completions`), cambiano URL base, modelli e formati accettati;
  - `gemini`: audio a `generateContent` per la trascrizione, seconda chiamata di solo
    testo per l'arricchimento;
  - `local` (futuro, fuori da questa fase): terzo adattatore con le stesse interfacce.
- **HTTP con `httpx`**, niente SDK dei provider: poche dipendenze, test con
  `httpx.MockTransport` senza rete. Timeout espliciti per chiamata.
- **Default dei modelli** (verificati sulla documentazione il 2026-09-29, modificabili
  nelle impostazioni; il piano li ricontrolla con il pulsante "Prova"):

  | Provider | Trascrizione | Testo | Note |
  |---|---|---|---|
  | Groq | `whisper-large-v3-turbo` | `openai/gpt-oss-120b` | ~$0,04/ora di audio; 25 MB per file sul piano gratuito |
  | Gemini | `gemini-3.8-flash` | `gemini-3.8-flash` | 20 MB per richiesta inline; ~32 token per secondo di audio |
  | OpenAI | `gpt-4o-mini-transcribe` | da scegliere quando si attiva | 25 MB per file |

- **Audio verso il provider**: il WAV viene convertito in **FLAC** (senza perdita, circa
  metà dimensione; una nota da 10 min passa da ~19 MB a ~10 MB) con `soundfile`, in un
  file temporaneo; in archivio resta il WAV. Ogni adattatore dichiara i formati che
  accetta: se il FLAC non è tra questi (da verificare per OpenAI) invia il WAV. Un audio
  che supera il limite del provider è un errore di contenuto (§9); la divisione in pezzi
  è fuori da questa fase.

## 5. Fallback tra provider

L'elaborazione di una fase usa il provider principale, poi le riserve nell'ordine delle
impostazioni, saltando quelli disabilitati o senza chiave.

- **Si passa al successivo** per: timeout, errore di rete, 5xx, 429, 401/403, risposta
  di arricchimento non JSON o fuori schema.
- **La nota fallisce subito** (nessun altro provider) per un errore di contenuto: 400/422
  sull'audio, audio oltre il limite di dimensione.
- Se falliscono tutti, il lavoro torna in coda con backoff (§7).
- Il provider che ha prodotto la trascrizione non vincola quello dell'arricchimento:
  ogni fase percorre la catena da capo.

## 6. File nella cartella del giorno

Accanto a `HHMMSS_<device>.wav` e al sidecar `.json` l'elaborazione scrive due file con
lo stesso nome base (sostituiscono il segnaposto `.transcript.md` della spec archivio):

**`HHMMSS_<device>.md`: nota leggibile, verità per i campi AI**

```markdown
---
title: Chiamare Marco per il preventivo
summary: Ricordarsi di chiamare Marco entro venerdì per il preventivo del tetto.
tags: [lavoro, casa, telefonate]
language: it
provider: groq
models: {transcribe: whisper-large-v3-turbo, enrich: openai/gpt-oss-120b}
processed_at: 2026-09-29T12:51:10+02:00
edited: [tags]
---
Allora, devo ricordarmi di chiamare Marco…
```

- Corpo = trascrizione. Frontmatter YAML con titolo automatico, riassunto, tag,
  lingua, provenienza ed `edited`: l'elenco dei campi (`transcript`, `summary`, `tags`)
  corretti a mano. Formato già leggibile da Obsidian.
- Dopo la sola fase di trascrizione il file esiste con il corpo e senza `title`,
  `summary`, `tags`.
- Il **titolo manuale** resta nel sidecar `.json`, come oggi. Titolo mostrato: manuale,
  altrimenti automatico, altrimenti il default per fascia del giorno.

**`HHMMSS_<device>.ai.json`: risposte grezze dei provider**

- Output originale di trascrizione e arricchimento, con provider, modelli e data. Mai
  modificato; "Rielabora" lo sostituisce.

**Coerenza con l'archivio**

- Scrittura atomica via `.incoming/` + rename, come il resto dell'archivio.
- Cestino, ripristino e "correggi data/ora" funzionano già su tutti i file `<base>.*`
  (`Archive.move`), e i `.ai.json` non vengono scambiati per sidecar (`_is_base_file`).
  I file compaiono tra i file correlati scaricabili della nota.
- `rescan` legge anche i `.md`: ricostruisce `transcript`, `title_auto`, `summary`,
  `tags`, `edited` e l'indice di ricerca. Una nota fuori dal cestino senza `.md` diventa
  "da elaborare" e va in coda con priorità bassa. Un `.md` illeggibile finisce nel
  report e **non cancella** i campi dal catalogo (stessa regola dei sidecar).

## 7. Coda e stati

**Schema (migrazione 0004)**

- Tabella `jobs`, un lavoro per nota: `capture_id` (unico, FK), `status`
  (`queued`, `running`, `done`, `failed`), `stage` (`transcribe`, `enrich`),
  `priority`, `attempts`, `next_run_at`, `locked_until`, `last_error`, `updated_at`.
- Colonne nuove su `captures`: `transcript`, `title_auto`, `summary`, `tags` (array di
  testo), `edited` (array di testo), `language`, `ai_provider`, `processed_at`,
  `search_vector` (`tsvector`, indice GIN) e indice GIN su `tags`.
- Tabelle delle impostazioni: `settings` (chiave → valore JSON) e `ai_providers`
  (nome, abilitato, ordine, chiave cifrata, modelli).
- Tabella `ai_usage` per provider e mese: secondi di audio trascritti, numero di
  chiamate.

**Chi mette in coda**

- **Ingest**: una nota accettata con `201` crea il suo lavoro nella stessa transazione,
  priorità normale. Il `409` duplicato non crea niente.
- **"Rielabora"** (UI) e `secondbrain process <capture-id>`: lavoro di nuovo `queued`,
  tentativi azzerati, `stage=transcribe` (o `enrich` se la trascrizione è stata
  corretta a mano, §10).
- **"Elabora le note senza trascrizione"** (impostazioni) e
  `secondbrain process --backfill`: tutte le note fuori dal cestino senza `.md`,
  priorità bassa.
- **`rescan`**: come il backfill, priorità bassa.

**Worker**

- Ciclo: se l'elaborazione è in pausa, o non c'è nessun provider utilizzabile, o manca
  `SETTINGS_KEY`, dorme; altrimenti prende il lavoro con priorità più alta e
  `next_run_at` più vecchio tra quelli `queued` scaduti o `running` con lease scaduto,
  con `FOR UPDATE SKIP LOCKED`, e imposta `locked_until`. Intervallo di polling e durata
  del lease sono costanti con nome.
- Fase `transcribe`: FLAC temporaneo, catena di provider, scrittura del `.md` con il
  solo corpo e del `.ai.json`, catalogo aggiornato, `stage=enrich`.
- Fase `enrich`: legge il testo dal catalogo, catena di provider, scrittura di titolo,
  riassunto e tag nel `.md` e nel catalogo, `status=done`.
- Un errore nella fase `enrich` non ripete la trascrizione.
- **Testo vuoto** (nessun parlato): `done` senza arricchimento, titolo di default.
- Le impostazioni si rileggono a ogni lavoro: cambi di chiavi, ordine e pausa valgono
  subito.

**Backoff e stato finale**

- Quando falliscono tutti i provider: nuovo tentativo dopo 1 min, 5 min, 30 min, 2 h,
  6 h; al sesto tentativo fallito `status=failed` con `last_error`. La nota fallita si
  vede nel finder e si rimette in coda con "Rielabora".
- Un errore di contenuto porta subito a `failed`.
- **Cestino**: spostare una nota nel cestino cancella il suo lavoro se non è `running`;
  se è `running`, il worker scarta il risultato al momento di scrivere. Il ripristino
  non rimette in coda.

## 8. Impostazioni e chiavi

**Pagina `/settings`** (icona ingranaggio, login + CSRF come il resto della UI)

- Elaborazione automatica: attiva / in pausa.
- Lingua (default `it`).
- Per ogni provider (Gemini, Groq, OpenAI): chiave API **in sola scrittura** (dopo il
  salvataggio si vede solo mascherata, es. `gsk_…a3f`), modelli di trascrizione e di
  testo precompilati coi default, abilitato sì/no, pulsante **"Prova"** che fa una
  chiamata gratuita (elenco dei modelli) e mostra l'esito.
- Ordine: principale e riserve, con frecce su/giù.
- Arretrato: pulsante "Elabora le note senza trascrizione" con il conteggio.
- Utilizzo: minuti di audio elaborati per provider nel mese corrente (niente euro).
- Stato della coda: in coda, in corso, fallite.

**Custodia delle chiavi**

- Cifrate in Postgres con **Fernet** (`cryptography`). La chiave `SETTINGS_KEY` sta solo
  nel `.env`; la genera `secondbrain gen-key`, il README spiega dove metterla. Un backup
  del solo database non rivela le chiavi; persa `SETTINGS_KEY`, si reinseriscono le
  chiavi API e nient'altro va perso.
- Senza `SETTINGS_KEY` la pagina lo dice, non si possono salvare chiavi e il worker non
  elabora; il resto del backend funziona come oggi.
- **Mai nei log, mai negli errori mostrati, mai nel browser.** Per Gemini la chiave va
  nell'header `x-goog-api-key`, non nell'URL. Gli errori dei provider vengono ripuliti
  (niente header, niente corpo della richiesta, lunghezza limitata) prima di finire in
  `last_error` e nei log.
- Le impostazioni sono configurazione: vivono solo in Postgres, `rescan` non le
  ricostruisce (come utenti e sessioni), il `pg_dump` del README le include.

## 9. Validazione dell'arricchimento

- Titolo: testo su una riga, al massimo 200 caratteri (limite della colonna `title`).
- Riassunto: al massimo 500 caratteri.
- Tag: minuscoli, senza `#` e spazi ai bordi, senza duplicati, al massimo 8, ciascuno al
  massimo 40 caratteri.
- Output non JSON o senza i campi richiesti = errore del provider (si passa al successivo).
- I limiti sono costanti con nome.

## 10. UI

**Lista del giorno**

- Titolo mostrato (manuale → automatico → default) e i primi 3 tag come etichette.
- Icona di stato accanto al titolo: in coda (orologio), in corso (rotella), fallita
  (triangolo); nessuna icona a elaborazione conclusa. Le righe in coda o in corso si
  aggiornano con htmx ogni pochi secondi finché non finiscono.

**Dettaglio della nota**

- Titolo modificabile come oggi, player, **riassunto**, **tag** cliccabili (aprono la
  ricerca per tag), **trascrizione**; riassunto, tag e trascrizione hanno la matita per
  la modifica inline con htmx (tag come testo separato da virgole, trascrizione in
  un'area di testo).
- Riga di provenienza: provider, modello di trascrizione, data dell'elaborazione, e "✎"
  accanto ai campi corretti a mano.
- Pulsante **"Rielabora"**; se ci sono campi corretti a mano, la conferma dice che
  resteranno invariati. Nota fallita: riquadro con l'errore ripulito e "Rielabora".
- Una modifica scrive il `.md` (campo aggiunto a `edited`) e il catalogo; lo stesso
  lavoro vale per la UI e per il worker: entrambi scrivono tenendo il lock sulla riga
  della nota e rileggono `edited` subito prima di scrivere, così una correzione non
  viene mai sovrascritta da un'elaborazione in corso.
- Con un `edited` non vuoto, "Rielabora" aggiorna solo i campi non corretti; se è stata
  corretta la trascrizione, la fase di trascrizione viene saltata e l'arricchimento
  parte dal testo corretto.

**Ricerca**

- Campo nell'intestazione (su mobile un'icona lente che lo apre), pagina `/search?q=`.
- `search_vector` è aggiornato dal codice a ogni scrittura dei campi (titolo manuale
  compreso), con un'unica
  espressione SQL: `setweight` A sul titolo mostrato (manuale o automatico), B su tag e
  riassunto, C sulla trascrizione, configurazione di testo derivata dalla lingua della
  nota (`it` → `italian`). Interrogazione con `websearch_to_tsquery` nella stessa
  configurazione.
- Esclude le note nel cestino. Ordine: pertinenza, poi data più recente.
- Ogni risultato: giorno ("Lunedì 29 set"), titolo, tag, estratto con le parole trovate
  evidenziate (`ts_headline`); il clic apre la nota nella sua giornata.
- `/search?tag=lavoro`: corrispondenza esatta sul tag, combinabile con `q`.
- Limite accettato: niente normalizzazione degli accenti ("perche" non trova "perché").

**Menu**: nuove voci Ricerca (lente) e Impostazioni (ingranaggio), icona + testo su
desktop, solo icona su mobile.

## 11. Casi limite

- Modifica dalla UI durante l'elaborazione della stessa nota: lock di riga + rilettura di
  `edited` (§10).
- Cestino o correzione della data durante l'elaborazione: il worker ricava il percorso
  dal catalogo al momento di scrivere; se la nota è nel cestino scarta il risultato.
- Disco pieno o errore di scrittura: il lavoro torna in coda con backoff.
- `.md` modificato a mano sul disco: `rescan` prende le modifiche valide; frontmatter
  illeggibile → report, catalogo invariato.
- Nessun provider configurato: le note restano `queued`, la pagina impostazioni e il
  finder lo segnalano.
- Nota nel cestino da prima dell'elaborazione: nessun lavoro, niente backfill.
- **Privacy**: il README dice che l'audio e il testo vanno al provider scelto, con la
  politica sui dati del proprio account.

## 12. Test e verifica

TDD su Postgres reale come l'archivio.

- **Adattatori** (`httpx.MockTransport`): forma delle richieste (chiave nell'header giusto,
  FLAC o WAV secondo il provider, lingua), parsing, classificazione degli errori; la
  chiave non compare mai nei log (`caplog`) né in `last_error`.
- **Conversione** WAV → FLAC: durata e campioni invariati.
- **Coda**: lease e ripresa dopo crash; due worker non prendono lo stesso lavoro; nota
  nuova prima dell'arretrato; backoff fino a `failed`; errore di contenuto subito
  `failed`; pausa; fase `enrich` ripresa senza ritrascrivere; testo vuoto; cestino
  durante l'elaborazione.
- **Fallback**: principale in 429 → riserva; tutti in errore → backoff; provider
  disabilitato o senza chiave saltato.
- **File**: scrittura e rilettura del `.md`; `edited` preservato da "Rielabora";
  `rescan` ricostruisce i campi e rimette in coda le note senza `.md`; `.md` rotto non
  cancella nulla.
- **Validazione** dell'arricchimento: limiti, normalizzazione dei tag, JSON non valido.
- **UI**: modifica inline dei tre campi, stato nella lista, "Rielabora", pagina
  impostazioni (chiave mascherata, mai nel HTML), CSRF sulle nuove azioni.
- **Ricerca**: stemming italiano ("chiamare" trova "chiamato"), pesi, esclusione del
  cestino, filtro per tag, estratto evidenziato.
- **Impostazioni**: cifratura e decifratura, `SETTINGS_KEY` assente o sbagliata.
- **Verifica finale col device vero** (sul Mac, poi sulla ZimaBoard con il deploy):
  note elaborate con Groq e con Gemini; chiave sbagliata sul principale → fallback;
  pausa e ripresa; backfill delle note già in archivio; correzione di un campo e
  "Rielabora"; ricerca dal telefono.

## 13. Fuori da questa fase

Adattatore locale; divisione in pezzi dell'audio oltre i limiti dei provider; vista per
tag; rielaborazione con un provider scelto a mano; cronologia delle versioni; costi in
euro; vault Obsidian e PiAgent; riconoscimento di chi parla; timestamp dei segmenti
nella UI.
