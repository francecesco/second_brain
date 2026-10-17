# Backend — "Da fare" come home, desktop con pannello e telefono con barra in basso — Design / Spec

**Data:** 2026-10-17
**Stato:** implementato sul branch `backend-ui-responsive` il 2026-10-17 (702 test del
backend; screenshot dell'app vera a 1440, 1024 e 390 px in chiaro e scuro, nessuno scroll
orizzontale; prove di interazione: un solo dettaglio aperto nel pannello, × che lo chiude,
spunta che aggiorna tessere e albero, foglio "Altro" che si chiude toccando fuori). **Provata
dall'autore sul Mac il 2026-10-17: approvata.** Deviazioni: l'ora nelle righe è `HH:MM`
ovunque, anche nella vista del giorno (data e secondi nel `title` dell'elemento `<time>`);
la tessera "Questa settimana" del mockup è "Ultimi 7 giorni" (oggi compreso); nel pannello
i tag non si ripetono in testa (ci sono già nel campo "Tag"); i mesi nell'albero vanno dal
più recente.

## 1. Obiettivo

L'interfaccia web nasce per il desktop. Sul telefono le stesse pagine vanno riadattate:
l'intestazione va a capo, l'albero dell'archivio occupa la cima di ogni pagina e i tasti
sono piccoli. La pagina usata più spesso è "Da fare", ma la home è l'archivio. Tre
obiettivi:

1. "Da fare" è la pagina di partenza;
2. sul telefono l'interfaccia funziona come un'app: navigazione in basso, righe compatte;
3. sul desktop la pagina è più ricca, nello stesso stile "pannello" (`docs/stile-ui.md`),
   con la navigazione dell'archivio per mesi sempre a sinistra.

## 2. Decisioni (approvate in chat il 2026-10-17 sui mockup a 390 e 1440 px)

**Comuni**

1. `/`, il login riuscito e il marchio portano a `/todo` (prima `/browse`).
2. "Da fare" e "Completati" sono **raggruppate per giorno**, con le intestazioni "Oggi",
   "Ieri", poi "Martedì 14 ottobre" (con l'anno se non è quello corrente). Da fare usa il
   giorno della registrazione, Completati il giorno in cui è stata spuntata. Nella riga
   resta solo l'ora (`09:42`). Sparisce la frase di spiegazione in cima alla lista.
   Un gruppo rimasto vuoto dopo una spunta sparisce.
3. Nel menu l'ordine diventa Da fare, Completati, Archivio, poi il resto.
4. Il filtro dispositivo esce dall'intestazione: sul desktop sta nella testata delle liste
   (Da fare, Completati, Archivio, Da sistemare), sul telefono nel foglio "Altro".

**Desktop (≥ 701 px)**

5. Colonna sinistra sempre presente (anche su Da fare, Impostazioni ecc.) in due sezioni:
   **Liste** (Da fare, Completati, con icona e contatore) e **Archivio** (anni a tessere;
   l'anno della pagina, o il più recente, è aperto con i mesi, e il mese aperto con i
   giorni). Ogni mese ha una barretta di densità in grigio, proporzionale al mese più
   pieno dell'anno. La colonna resta ferma quando si scorre.
6. Su "Da fare" la testata ha quattro **tessere-contatore**: Da fare, Oggi, Ultimi 7
   giorni, In elaborazione (con il LED arancione se ce n'è almeno una in corso). La
   tessera "Da fare" è nello stato corrente (bordo e numeri d'accento). Si aggiornano
   out-of-band quando si spunta una nota.
7. Righe a colonne: casella, ora, titolo con i tag sotto, dati a destra; leggera
   evidenziazione al passaggio del mouse.
8. **Pannello del dettaglio a destra** da 1100 px in su: il clic su una riga apre il
   dettaglio in una colonna fissa a destra (27 rem), con in testa ora d'accento, giorno e
   dispositivo, titolo, tag e la × per chiudere; la riga aperta ha un filetto d'accento a
   sinistra. Una sola nota aperta per volta. Sotto i 1100 px il dettaglio si apre nella
   riga come oggi. È lo stesso elemento `.detail` della riga, posizionato dal CSS:
   htmx, azioni e test non cambiano.

**Telefono (≤ 700 px)**

9. Niente intestazione in alto e niente albero: in cima una riga fissa con il punto
   arancione, il nome della pagina e il contatore.
10. **Barra in basso** fissa: Da fare, Archivio, Nuova (unico tasto pieno arancione),
    Cerca (pagina `/search`), Altro. "Altro" apre un foglio dal basso con Completati, Tag,
    Da sistemare, Cestino, Dispositivi, Impostazioni, il filtro dispositivo ed Esci; si
    chiude toccando fuori o con Esc. La barra rispetta la zona del gesto home
    (`safe-area-inset-bottom`, `viewport-fit=cover`).
11. Righe compatte: ora (16 px) in colonna fissa, titolo (15 px) accanto, tag e dati
    sotto; casella da 20 px. Il pannello occupa tutta la larghezza.
12. Dettaglio: etichette sopra i valori; titolo e data su righe intere; Rielabora,
    Cestino e Chiudi in tre tasti con il testo.
13. Campi a 16 px (iOS non ingrandisce la pagina al tocco) e tasti più alti al tocco
    (`pointer: coarse`), sempre una taglia sola per tutti.

## 3. Cosa non cambia

Rotte, contratto con il device, dati, htmx delle azioni (`#row-…`, `#detail-…`),
stile "pannello" (token, font, regole dell'arancione). La vista per giorno dell'archivio
resta in ordine cronologico.

## 4. Modifiche

- `web/login.py`: redirect a `/todo`.
- `web/context.py`: albero con l'anno aperto di default e il massimo mensile; raggruppamento
  per giorno; tessere di "Da fare".
- `web/actions.py`: Da fare e Completati raggruppati, risposta della spunta con le tessere
  out-of-band.
- Template: `finder.html` (testata mobile, barra in basso, foglio Altro), `_tree.html`,
  `_row.html`, `_detail.html` (testata del pannello), `todo.html`, `done.html`, nuovi
  `_tiles.html` e `_day_groups.html`; `base.html` (chiusura del foglio, un solo dettaglio
  aperto nel pannello).
- `style.css` e `docs/stile-ui.md`.

## 5. Verifica

`uv run pytest -q`; screenshot dell'app vera (fixture dei test) a 1440, 1024 e 390 px in
chiaro e scuro: Da fare, Da fare con nota aperta, Completati, Archivio (mese e giorno),
Impostazioni, Cestino, Nuova nota, Ricerca, foglio "Altro". Nessuno scroll orizzontale.
Poi prova dell'autore sul Mac (`docker compose up -d --build`, http://localhost:8000) e
dal telefono; il deploy sulla ZimaBoard lo decide l'autore.
