# Backend — Note da fare e dettaglio a fisarmonica — Design / Spec

**Data:** 2026-10-12
**Stato:** implementato sul branch `backend-note-da-fare` (546 test del backend, screenshot a
1280 e 390 px in chiaro e scuro); in prova sul Mac dall'autore. **Cambiato dopo la prima
prova dell'autore (2026-10-12)**: la nota spuntata non resta barrata nell'archivio ma
**esce dalla lista** (archivio, albero e "Da fare") e passa nella nuova vista
**"Completati"** (dall'ultima spuntata), da cui togliendo la spunta torna indietro; la
ricerca trova anche le completate, barrate. Le voci dell'intestazione stanno in un **menu
a scomparsa**, senza la voce "Ricerca" (c'è già il campo "Cerca nelle note"). Spuntare
aggiorna out-of-band i contatori del menu e l'albero, letti con il filtro dispositivo e la
data della pagina corrente (`HX-Current-URL`). Nei risultati di ricerca la casella non
c'è: si spunta dall'archivio, da "Da fare" o da "Completati".

## 1. Obiettivo

Le note vocali sono spesso cose da affrontare. L'autore vuole spuntarle man mano che le
affronta, **senza eliminarle**, e vedere a colpo d'occhio quelle ancora aperte. Inoltre il
clic sul titolo di una nota deve **aprire e chiudere** il dettaglio (oggi lo apre soltanto;
per chiuderlo serve il pulsante "Chiudi").

## 2. Decisioni (approvate in chat il 2026-10-12)

1. **Ogni registrazione è un punto spuntabile, intera**: il contenuto non si divide in punti.
2. **Spuntata = fatta**: resta dov'è nell'archivio, nella ricerca e nei tag, con il titolo
   barrato e attenuato; si può togliere la spunta.
3. **Vista "Da fare (N)"** nella barra, come "Da sistemare (N)": tutte le registrazioni non
   fatte e non nel cestino, dalla più recente, rispettando il filtro del dispositivo.
   Spuntarne una la toglie dalla lista.
4. Le registrazioni già archiviate partono **tutte da fare**.

## 3. Dove sta lo stato

Lo stato è una scelta dell'utente, come il titolo manuale, quindi segue la stessa strada:

- **Sidecar `.json`** (verità, sopravvive a `rescan`): campo `done_at`, istante UTC in cui
  è stata spuntata, `null` se da fare.
- **Catalogo Postgres**: colonna `captures.done_at` (migrazione `0006`), indice parziale
  per la vista "Da fare". `rescan` la ricostruisce dal sidecar.
- **Nota nel vault**: nel frontmatter `completata: true/false`, scritto dal backend come
  `title` e `url`, **non riletto** (spec vault §5): in Obsidian si vede e si può filtrare,
  ma si cambia dall'app. Ogni spunta riscrive il frontmatter.
- Cestino e ripristino non toccano `done_at`.

## 4. Interfaccia

- **Casella** all'inizio di ogni riga (archivio, ricerca, tag, "Da fare", "Da sistemare"),
  fuori dal pulsante della riga (un input dentro un `<button>` non è valido). Clic →
  `POST /captures/{id}/done` (o `/undone`) con htmx, risposta = la riga aggiornata.
  Etichetta accessibile "Segna come fatta" / "Segna come da fare".
- **Riga fatta**: titolo barrato e testo attenuato con i token esistenti; nessun nuovo
  colore. Il controllo segue `docs/stile-ui.md` (dimensioni dei bersagli al tocco a 390 px).
- **"Da fare (N)"** nella barra con icona propria, tra "Tag" e "Da sistemare"; pagina
  `/todo` con le righe in elenco unico (data in ogni riga, come la ricerca).
- **Titolo a fisarmonica**: il clic sul pulsante della riga, se il dettaglio è aperto, lo
  svuota invece di ricaricarlo (`hx-get` solo se vuoto; altrimenti `innerHTML = ''`).
  `aria-expanded` sul pulsante segue lo stato. Il pulsante "Chiudi" resta.

## 5. Modifiche

- `models.py`, migrazione `0006_done_at.py`, `sidecar.py` (`done_at` nei campi), `catalog.py`
  (lista e conteggio "da fare"), `rescan.py`, scrittura del frontmatter nel vault.
- `web/actions.py` (rotte done/undone, CSRF e auth come le altre azioni), `web/browse.py`
  o nuovo `web/todo.py` (pagina `/todo`), `context.py` (conteggio per la barra).
- Template `_row.html` (casella, classe `done`, `aria-expanded`), `finder.html` (voce di
  menu), `todo.html`, `_icons.html` (icona), `style.css`.
- Test: azioni done/undone (sidecar, catalogo, vault), vista e conteggio "Da fare" con
  filtro dispositivo e cestino, rescan che conserva `done_at`, frontmatter, pagina col
  rendering della casella e del titolo a fisarmonica.

## 6. Verifica

1. Sul Mac, test del backend verdi.
2. Screenshot a 1280 e 390 px: righe da fare e fatte, vista "Da fare", dettaglio aperto e
   richiuso col titolo, controllati contro `docs/stile-ui.md`.
3. Sulla ZimaBoard (deploy con `git archive`): spunta di una nota dal telefono, sparisce
   da "Da fare", resta barrata nell'archivio, `completata: true` nel `.md` del vault.
