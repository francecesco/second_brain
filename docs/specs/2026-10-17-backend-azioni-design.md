# Backend — Azioni e scadenze estratte dalle note — Design / Spec

**Data:** 2026-10-17
**Stato:** approvata in chat il 2026-10-17; in sviluppo sul branch `backend-azioni`.

## 1. Obiettivo

"Da fare" ragiona per nota intera: una registrazione con tre cose da fare, o con
"entro venerdì", resta un blocco unico e senza data. L'elaborazione AI ricava dalla nota
le **azioni** (cose concrete che l'autore deve fare) e, quando la nota la dice, la
**scadenza**. Le azioni si spuntano una per una, le scadenze emergono in cima a "Da fare" e
sulla schermata "Oggi" del device.

## 2. Decisioni (approvate in chat il 2026-10-17)

1. Le azioni sono **sottopunti della nota**: sotto ogni nota in "Da fare", ciascuna con la
   sua casella. Le note senza azioni (idee, appunti) restano come oggi.
2. La nota passa in "Completati" quando **tutte** le sue azioni sono spuntate; spuntare la
   nota intera spunta anche tutte le sue azioni. Togliere la spunta a un'azione di una nota
   completata la riporta in "Da fare".
3. **Scadenze come date**: l'AI converte "venerdì", "domani", "entro fine mese" in una data,
   rispetto al giorno e all'ora della registrazione. In cima a "Da fare" una sezione **In
   scadenza** con "Scadute", "Oggi", "Prossimi 7 giorni".
4. La schermata **"Oggi" del device** mostra le azioni scadute e di oggi.
5. **Home Assistant solo come contesto**: gli eventi del calendario dei prossimi giorni
   (già letti dal worker) vanno all'AI per capire date e impegni. Nessuna scrittura su Home
   Assistant: il connettore resta in sola lettura.

## 3. Modello

Un'azione: `id` (8 caratteri esadecimali casuali, stabile), `text` (una riga, al massimo
200 caratteri), `due` (data o niente), `done` (istante della spunta o niente), in ordine.
Al massimo **10** azioni per nota.

- **Nota `.md` nel vault** (verità dei campi AI e delle correzioni, come titolo, riassunto e
  tag): nel frontmatter una lista `actions`, per esempio
  `- {id: 3f9a1c20, text: Chiamare Marco per il preventivo, due: 2026-10-23, done: null}`.
  `actions` diventa un campo modificabile: compare in `edited` quando l'autore aggiunge,
  modifica, cancella **o spunta** un'azione, e da lì "Rielabora" non la riscrive più
  (come oggi per riassunto, tag e trascrizione).
- **Catalogo Postgres**: colonna `captures.actions` (JSONB, lista vuota di default),
  migrazione `0007`, come gli altri campi AI: si scrive insieme alla nota (`write_note`) e
  `rescan` la ricostruisce dai `.md`. Le viste "In scadenza" e le tessere filtrano in Python
  le note aperte con azioni (uso personale: centinaia di note, non milioni). Scelta presa
  nel piano al posto della tabella separata della prima bozza: stessa strada di `tags`,
  nessun codice di sincronizzazione in più.
- **Spunta della nota** (`done_at` nel sidecar, spec note da fare §3): resta la verità per
  "Completati"; le regole del §2.2 la tengono allineata alle azioni.

## 4. Elaborazione AI

- Il prompt di arricchimento chiede una quarta chiave, `"actions"`: lista di
  `{"text", "due"}` (`due` come `AAAA-MM-GG` o `null`), vuota se la nota non contiene cose
  da fare. Solo azioni dell'autore, all'infinito o all'imperativo, una riga ciascuna; niente
  azioni ricavate da semplici idee o osservazioni.
- Contesto nel prompt: data, giorno della settimana e ora della registrazione (fuso
  dell'archivio), e gli eventi dei calendari scelti nei 14 giorni successivi dall'istantanea
  di Home Assistant, se c'è, per risolvere "prima della riunione con Marco".
- Validazione: azioni vuote o duplicate scartate, testo tagliato a 200, date non valide
  o più vecchie di un anno → senza scadenza. Una risposta senza `actions` vale "nessuna
  azione" (provider che non la producono non rompono l'arricchimento).
- Note già archiviate: nuova fase `actions` (come `retag`) che rifà solo le azioni, a
  priorità bassa, per le note non completate e senza azioni corrette a mano: pulsante nelle
  Impostazioni e `secondbrain actions --all` / `secondbrain actions <id>`.
- Note scritte dall'app: stessa estrazione (sono già in fase `enrich`).

## 5. Interfaccia

**Riga di una nota** (Da fare, archivio, ricerca): sotto titolo e tag, le azioni come
righe piccole: casella da 16 px, testo, scadenza come etichetta (`ven 23/10`). Scadenza
passata: etichetta d'accento (è "attenzione"). Azione fatta: barrata in `--mute`. Spuntare
un'azione aggiorna la riga, i contatori, le tessere e la sezione In scadenza out-of-band.

**In scadenza** (in cima a "Da fare", prima dei gruppi per giorno): tre gruppi, Scadute,
Oggi, Prossimi 7 giorni, che elencano **azioni** (casella, testo, scadenza, titolo della
nota come link che apre la nota). Sezione nascosta se vuota.

**Tessere di "Da fare"**: Da fare (note), Azioni aperte, In scadenza (scadute + oggi; numero
d'accento se ci sono scadute), In elaborazione.

**Dettaglio della nota**: campo "Azioni" con la matita, come gli altri campi AI. In modifica
una riga per azione (testo e data), "Aggiungi", "×" per togliere, "Salva"/"Annulla".

**Telefono**: stesse righe, casella da 20 px.

## 6. Schermata "Oggi" del device

Prima degli eventi, fino a 3 righe di azioni: scadute con il prefisso `! `, poi quelle di
oggi; "+N" se sono di più. L'immagine si compone anche **senza Home Assistant** se ci sono
azioni da mostrare (oggi senza Home Assistant `GET /digest/today` risponde `204`). L'ETag
cambia quando cambiano le azioni mostrate, quindi il device ridisegna solo allora.

## 7. Cosa non cambia

Firmware, contratto con il device, spunta della nota intera per le note senza azioni,
vault (le azioni stanno nel frontmatter; il corpo resta la trascrizione).

## 8. Modifiche

- `notefile.py`: `Action`, campo `actions` in `Note`, render/parse, `validate_enrichment`.
- `ai/base.py`: prompt e contesto (data della registrazione, eventi del calendario).
- `models.py` + migrazione `0007_actions.py` (colonna JSONB); `notes.py`, `catalog.py`.
- `library.py`: spunta di un'azione e regole del §2.2; `jobs.py` + `cli.py`: fase `actions`.
- Web: route `POST /captures/{id}/actions/{aid}/done|undone`, modifica del campo, template
  della riga, della sezione In scadenza, delle tessere e del dettaglio; `stile-ui.md`.
- `digest.py`, `digest_api.py`: righe delle azioni, immagine anche senza Home Assistant.

## 9. Verifica

Test: validazione della risposta (azioni mancanti, vuote, date strane), date relative
risolte in un caso concreto con un provider finto, regole di spunta nota/azioni, rescan,
"Rielabora" con azioni corrette, sezione In scadenza al cambio di giorno (orologio finto),
digest con e senza Home Assistant. Poi prova vera con Groq/Gemini su 5–10 note dell'archivio
(fase `actions`), screenshot a 1440 e 390 px, prova dell'autore sul Mac.
