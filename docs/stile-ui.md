# Stile della UI web — linee guida

Riferimento per chi tocca le pagine del backend (`backend/secondbrain/web/`). Lo stile
si chiama "pannello": ispirato alle interfacce industriali minimali (Teenage Engineering,
Nothing), scelto il 2026-10-06 tra tre direzioni con mockup. Il foglio di stile è uno
solo, `web/static/style.css`; questa guida spiega le regole che quel file applica, così
una pagina nuova nasce coerente senza dover leggere tutto il CSS.

## Principi

1. **Nessuna libreria grafica, nessuna CDN.** Il CSS è scritto a mano su variabili CSS;
   htmx e i font sono vendorizzati in `web/static/`. Il backend gira in casa, dietro un
   tunnel: tutto quello che la pagina carica lo serviamo noi. Niente build step.
2. **Un solo colore d'accento, con un significato.** L'arancione dice "qui" (dove sei,
   cosa è aperto, l'azione principale) oppure "attenzione" (in corso, errore, avviso).
   Non è decorazione: se un elemento arancione non comunica una di queste due cose, va
   tolto.
3. **Pannelli su fondo grigio.** La pagina è un fondo grigio caldo su cui poggiano
   pannelli chiari con bordo sottile e angoli di 2 px: intestazione e contenuto. Dentro
   un pannello, le sezioni si separano con hairline, non con altri riquadri. L'albero a
   sinistra non è un pannello ma una colonna di tessere, una per voce, con angoli di
   6 px: la stessa forma delle tessere di anni, mesi e giorni nel contenuto.
4. **Etichette in maiuscoletto, numeri in monospazio, orari a matrice di punti.** Tutto
   ciò che è etichetta (menu, briciole, intestazioni di tabella e di sezione, termini
   delle definizioni) è piccolo, maiuscolo e spaziato. Tutto ciò che è identificativo
   (dimensioni, id, hash, modelli, etichette delle tessere) è in Space Mono con cifre
   tabulari. Gli orari delle righe, il marchio e i contatori delle tessere sono in Doto,
   il font a matrice di punti: è il "display" dell'interfaccia, come quello del device.
5. **Tema chiaro e scuro dalla preferenza del sistema.** Nessun interruttore in pagina.
   Ogni colore passa da una variabile: se una regola nuova usa un colore letterale, nel
   tema scuro si rompe.
6. **Mobile senza pagine separate.** Sotto i 700 px la stessa pagina si impila; sotto i
   1280 px il menu mostra solo le icone. Ogni azione ha `aria-label` e `title`, perché
   su telefono l'etichetta scompare.

## Token

Definiti in `:root` e ridefiniti in `@media (prefers-color-scheme: dark)`.

| Variabile | Chiaro | Scuro | Uso |
|---|---|---|---|
| `--bg` | `#e3e3df` | `#171716` | fondo pagina, campi di input, tessere della griglia |
| `--panel` | `#f6f6f3` | `#232322` | pannelli, pulsanti, intestazione, tessere dell'albero |
| `--fg` | `#1a1a1a` | `#ececea` | testo |
| `--mute` | `#74746f` | `#92928c` | testo secondario, etichette, orari, contatori |
| `--line` | `#c9c9c3` | `#3b3b38` | bordi e hairline |
| `--acc` | `#ff4d00` | `#ff5a14` | accento (vedi "Dove va l'arancione") |
| `--acc-fg` | `#fff` | `#fff` | testo sopra l'accento |
| `--acc-edge` | `#b53600` | `#9e3300` | bordo inferiore del pulsante principale |
| `--acc-bg` | `#ffe8dd` | `#3a2315` | fondo degli avvisi |
| `--mark-bg` | `#ffd3bd` | `#5a2a10` | evidenziazione dei risultati di ricerca |
| `--sans` | Space Grotesk, poi Helvetica Neue, Arial, system-ui | | testo |
| `--mono` | Space Mono, poi ui-monospace, Menlo | | identificativi e numeri secondari |
| `--dot` | Doto, poi `--mono` | | orari delle righe, marchio, contatori delle tessere |

I tre font sono squadrati, nello spirito di Nothing, e tutti con licenza OFL (file
`*-OFL.txt` accanto a ciascuno in `web/static/`): Space Grotesk variabile 300–700
(22 KB), Space Mono 400 (9 KB), Doto variabile 100–900 (5 KB), tutti solo sottoinsieme
latino. Se servono altri glifi si rigenera il sottoinsieme, non si aggiunge un secondo
file per peso. Il tipo `font/woff2` è registrato in `app.py` perché l'immagine slim non lo
conosce.

## Tipografia

- Corpo: 14 px, interlinea 1,5, Space Grotesk.
- Etichetta (classe `.lab`, applicata anche a menu, briciole, `th`, `h2` delle card,
  `h3` dei campi AI, `dt` del dettaglio, tag): 10,5 px, maiuscolo, spaziatura 0,14 em,
  peso 600, colore `--mute`. Le `h2` delle card sono a 12 px e in `--fg`.
- Titolo di una riga: peso 500. Voce corrente dell'albero: stesso peso, colore `--acc`.
- Etichetta delle tessere (albero e griglia): Space Mono 11 px, maiuscolo, spaziatura
  0,1 em, peso 400.
- Monospazio (`.mono`, `.count`, `.meta`, `.provenance`, `td.mono`): Space Mono è largo,
  quindi 0,75–0,85 em quando è secondario, sempre `font-variant-numeric: tabular-nums`.
- Matrice di punti (`--dot`): orari delle righe a 18 px peso 800, marchio a 19 px peso
  800, titolo del login a 22 px, contatori delle tessere a 16 px peso 700. Mai sotto i
  16 px: a dimensioni piccole i punti non si leggono, per i numeri secondari (contatori
  nel testo, durate, dimensioni) e gli id si usa il monospazio.
- Niente titoli grandi: la gerarchia la fanno le etichette, gli spazi e le hairline.

## Dove va l'arancione

Sì, e solo qui:

- voce di menu attiva (classe `on`: testo e sottolineatura di 2 px);
- voce corrente dell'albero (classe `current`: testo e contatore; il bordo solo sulla
  voce più profonda, non sugli antenati) e punto del marchio;
- orario della riga di cui è aperto il dettaglio (`.row:has(> .detail:not(:empty)) .time`);
- titolo di una riga o di un risultato al passaggio del mouse;
- pulsante principale (`button.pri`), uno per modulo: salva, entra (in una vista con più
  moduli separati, come le Impostazioni, uno per modulo);
- pulsante "Nuova nota" nell'intestazione: l'unico elemento pieno arancione della barra. Su
  `/new` è nello stato `on` (non pieno), così "Salva" resta l'unico pulsante pieno della vista
  (che ha un solo modulo);
- LED "in corso" (`.led.run`) e icona "fallita" (`.status.failed`);
- avvisi (`.notice`, `#flash`): bordo sinistro, icona e link;
- marcatore "corretto a mano" (`.edited`), badge "data stimata" (`.badge`), errori di
  modulo (`.error`), esito negativo di una prova (`.test-result.ko`);
- anello di focus da tastiera.

No:

- tag, briciole, intestazioni di sezione, bordi dei pannelli;
- contatori, salvo quello della voce corrente dell'albero, che segue il suo testo;
- link normali: sono in `--fg` e passano a `--acc` solo al passaggio del mouse;
- più di un pulsante pieno nello stesso modulo.

## Componenti

**Pannelli e layout.** `header.top` (intestazione, bordo inferiore), `.layout` con
`aside.tree` (15 rem) e `main`, separati da 8 px di fondo grigio. Una pagina nuova
estende `finder.html` e riempie `{% block main %}`: eredita intestazione, albero e
briciole. Le briciole sono etichette con `›` tra una e l'altra.

**Albero** (`aside.tree`). Ispirato ai menu a schede di Nothing: liste annidate di
tessere, ogni voce è un `<a>` con l'etichetta a sinistra (Space Mono, 11 px, maiuscolo,
spaziatura 0,1 em) e il contatore a destra dentro lo stesso link (`.count`, Doto 16 px).
Tessera: fondo `--panel`, bordo 1 px, angoli 6 px, altezza minima 2,6 rem, 4 px di
spazio tra una e l'altra, ogni livello rientrato di 12 px. La voce corrente ha testo e
contatore d'accento; il bordo d'accento solo sulla voce più profonda. Le tessere della
griglia nel contenuto (`.grid a`) hanno la stessa forma.

**Pulsanti.** `<button>` ha già lo stile: fondo `--panel`, bordo 1 px, raggio 2 px,
ombra `0 2px 0 var(--line)` che fa da bordo inferiore, testo in etichetta. Al passaggio
il bordo diventa `--fg`, alla pressione il tasto scende di 1 px. `button.pri` è pieno
d'accento. I pulsanti con sola icona tengono l'etichetta in `<span class="label">`, che
sparisce sotto i 700 px. Dentro `.row-main` e `.top nav` i pulsanti sono "nudi" (niente
bordo né ombra): sono righe e voci di menu, non tasti.

**Misure dei pulsanti.** Una sola taglia per tutti i tasti: `button`, `a.btn`, il
`summary` del menu e "Nuova nota". Padding `.35rem .7rem`, etichetta a 10,5 px (maiuscolo,
spaziatura 0,12 em, peso 600), `line-height` 1,4, icona a 1,1 em (la regola `.icon`, mai
ingrandita), bordo 1 px e ombra di 2 px: circa 30 px di altezza, la stessa dei campi
dell'intestazione. Regole:

- un tasto si distingue per **colore** (pieno d'accento, stato `on`), **mai per misura**:
  niente `min-height`, padding o icone propri. Un tasto nuovo parte da `button`/`.btn` e
  cambia solo i colori;
- sotto i 700 px cambia solo l'etichetta, che sparisce; padding e icona restano uguali;
- nella stessa riga (intestazione, `.actions`) tutti i tasti hanno la stessa altezza;
- nessuna eccezione per pagina o modulo: anche "Salva"/"Annulla" di Nuova nota ed
  "Entra" del login hanno la taglia comune. La **larghezza** può seguire il layout del
  modulo ("Entra" occupa tutta la riga del login), l'altezza no;
- al massimo un tasto pieno per modulo (vedi "Dove va l'arancione"); una vista con un solo
  modulo ne ha quindi uno solo.

Non sono tasti, e quindi non seguono la taglia: le righe (`.row-main`) e le voci del menu
(`.top nav`), già descritte sopra, e la matita "modifica" accanto ai titoli dei campi del
dettaglio (`.pencil`), un'icona nuda in linea col testo, senza bordo né ombra finché non
ci si passa sopra.

**Link con forma di pulsante** (`a.btn`). Stesso aspetto e stesse misure di `button`
(bordo, hover, altezza), per azioni di navigazione che non inviano un modulo: "Annulla"
nella pagina Nuova nota, con icona. Mai pieno.

**Campi.** `input`, `select`, `textarea`: fondo `--bg` (incassato rispetto al pannello),
bordo 1 px, raggio 2 px. Nelle card delle impostazioni, che hanno già fondo `--bg`, i
campi passano a `--panel`. Il focus è un anello di 2 px d'accento.

**Menu** (`details.menu` in `finder.html`). Tutte le sezioni stanno in un menu a
scomparsa a destra dell'intestazione, accanto alla ricerca: il pulsante (`summary`, stile
dei pulsanti) mostra l'icona e il nome della sezione corrente (solo l'icona sotto i
700 px); il pannello è un elenco verticale di voci con icona ed etichetta, la voce
corrente con bordo sinistro e testo d'accento, "Esci" separato da una hairline in fondo.
Si chiude cliccando fuori o con Esc (`base.html`). I contatori "Da fare" e "Completati"
stanno in `_nav_counts.html`, che le risposte htmx rimandano out-of-band. Non c'è una
voce "Ricerca": basta il campo "Cerca nelle note".

**Righe della lista** (`_row.html`). `.row` con hairline sotto, a due colonne: a sinistra
la casella "fatta" (`.check`), a destra `.row-main`, un `<button>` che apre il dettaglio
in `.detail` e lo richiude al secondo clic (`toggleDetail` in `base.html`, che tiene
aggiornato `aria-expanded`); il dettaglio occupa tutta la larghezza. Nel cestino la
casella non c'è e `.row-main` prende entrambe le colonne. Ordine dentro il pulsante:
orario (`--mute`, Doto), titolo, tag, LED di stato, meta a destra (dispositivo, durata,
dimensione in monospazio). La riga aperta si riconosce dall'orario arancione. Spuntata,
la riga esce dalla lista in cui sta (archivio e "Da fare" → "Completati", e viceversa).

**Casella "fatta"** (`.row .check`). Quadrato di 18 px, bordo 1,5 px `--mute`, angoli
2 px, fondo `--bg`; spuntata è piena di `--fg` con la spunta in `--panel`. **Non è
arancione**: "fatta" non è né "qui" né "attenzione". La `label` attorno fa da bersaglio
al tocco (36×44 px). Riga fatta (`.row.done`, nella vista "Completati" e barrata nei
risultati di ricerca): titolo barrato in `--mute`; resta leggibile e cliccabile.

**LED di stato** (`_row_head.html`). `.led.queue`: anello grigio, in coda. `.led.run`:
disco arancione che lampeggia (fermo con `prefers-reduced-motion`). Fallita: icona
triangolo con `.status.failed`. Ogni LED ha `role="img"` e `aria-label`.

**Tag.** `.tags .tag`: bordo 1 px, etichetta a 9,5 px. Se è un link, al passaggio bordo
e testo diventano d'accento. Mai riempiti di colore.

**Avvisi.** `.notice` in pagina e `#flash` fisso in alto: fondo `--acc-bg`, bordo
sinistro di 3 px d'accento, testo in `--fg`, icona e link in `--acc`. `#flash[hidden]`
resta nascosto: non dare `display` a elementi che usano l'attributo `hidden` senza
coprire quel caso.

**Card** (impostazioni). `.card`: fondo `--bg` dentro il pannello, bordo, `h2` in
etichetta. I moduli dentro usano `.settings-form` (griglia a una colonna, larghezza
massima 32 rem) e `.actions` per i pulsanti.

**Righe con suggerimenti** (`.ha-sensor`, card Home Assistant). Un `<input list>` con
`<datalist>` per scegliere tra centinaia di voci scrivendo; accanto, in `--mute`, il nome
leggibile della voce scelta (le opzioni portano `label`), e `.count` "non trovato" se la
voce salvata non esiste più. Il server rivalida sempre il valore.

**Anteprima di un'immagine a bassa risoluzione** (`.ha-preview`). Raddoppiata (400 px) con
`image-rendering: pixelated`, bordo hairline, `max-width: 100%`: i pixel restano quadrati
come sul device.

**Tabelle** (cestino, dispositivi). `th` in etichetta con hairline in `--fg` sotto
l'intestazione, `td` con hairline in `--line`, avvolte in `.table-wrap` per lo scroll
orizzontale su telefono.

**Icone.** Macro `icon(nome)` in `_icons.html`: SVG inline 24×24 a tratto, `stroke:
currentColor`, `aria-hidden`. Un'icona nuova si aggiunge lì, disegnata a mano nello
stesso tratto; niente icon font, niente immagini. Un'icona non più usata si toglie.

## Breakpoint

- `≤ 1280 px`: il menu mostra solo le icone (la riga dell'intestazione resta una).
- `≤ 700 px`: layout a blocchi, albero richiudibile con l'etichetta "Archivio ▾",
  ricerca dietro l'icona lente, meta delle righe su una riga propria, etichette dei
  pulsanti nascoste.

## Come si aggiunge una pagina o un componente

1. Estendere `finder.html`, usare le classi esistenti prima di inventarne di nuove.
2. Se serve una classe nuova: nome in inglese breve come le altre, colori solo da
   variabili, misure in `rem`/`em` tranne hairline e raggi.
3. Chiedersi se l'elemento nuovo merita l'arancione secondo la lista sopra. In dubbio, no.
4. Testi in italiano, etichette brevi; ogni controllo con sola icona ha `aria-label`
   e `title`.
5. Se il template cambia la struttura (classi su cui i test fanno asserzioni:
   `brand`, `crumbs`, `count`, `tags`, `tag`, `title`, `actions`, `search-open`, `on`),
   aggiornare o aggiungere il test in `backend/tests/`.

## Verifica visiva

Prima di chiudere una modifica di stile:

1. `cd backend && uv run pytest -q` (serve il Postgres di test, vedi `backend/README.md`).
2. Screenshot headless con Chrome delle pagine toccate, in chiaro, scuro e a 390 px.
   Il modo più semplice: un test temporaneo che usa la fixture `recordings`, imposta
   titolo, tag e trascrizione su una cattura, salva le pagine come HTML con i link a
   `/static/` riscritti in percorsi `file://` (attenzione al `?v=` della versione, vedi
   sotto), e due copie del CSS in cui il blocco
   `@media (prefers-color-scheme: dark)` diventa `@media all` (scuro) o `@media not all`
   (chiaro). Chrome headless su Mac segue il tema del sistema e non scende sotto ~500 px
   di finestra: per il telefono si mette la pagina in un `<iframe>` largo 390 px.
3. Guardare gli screenshot davvero: intestazione su una riga, niente scroll
   orizzontale, LED e orario arancione dove previsto, contrasto nel tema scuro.
4. `docker compose up -d --build` e controllo sul sito vero, ricaricando la pagina.

**File statici versionati.** `base.html` carica CSS e htmx con `static_url(...)`, che
aggiunge `?v=` con l'hash del contenuto: Cloudflare tiene in cache i `.css` per ore e,
senza, dopo un deploy il tunnel serviva il foglio di stile vecchio con l'HTML nuovo
(2026-10-12). Un file statico nuovo caricato da una pagina passa da `static_url`.

## Storico delle decisioni

- **2026-10-06** — Direzione scelta tra tre mockup (pannello alla Teenage Engineering,
  dot matrix alla Nothing, 1-bit come il device): pannello, con dose "moderata" di
  arancione (la lista "Dove va l'arancione"). Prima versione con Helvetica e JetBrains Mono.
- **2026-10-06** — Font squadrati nello spirito di Nothing: Space Grotesk, Space Mono,
  Doto per orari e marchio.
- **2026-10-07** — Albero a tessere ispirato ai menu a schede di Nothing, contatori in
  Doto, stessa forma per le tessere della griglia; bordo d'accento solo sulla voce più
  profonda.
- **2026-10-12** — Pulsante "Nuova nota" nell'intestazione, unico pieno arancione della
  barra, nello stato `on` su `/new` perché lì "Salva" è il pulsante pieno; "Annulla" come
  link `a.btn`; icona matita riusata.
- **2026-10-12** — "Nuova nota" alto 40 px e con l'icona ingrandita al telefono stonava
  accanto a "Menu": riportato alle misure comuni dei tasti, regole scritte in "Misure dei
  pulsanti" (un tasto si distingue per colore, mai per misura). Tolte anche le misure
  proprie di "Salva"/"Annulla" in Nuova nota (2,5 rem, etichetta sempre visibile) e di
  "Entra" nel login (padding maggiorato).
- **2026-10-13** — Card Home Assistant: righe con `datalist` e nome in `--mute`, anteprima
  pixelata 2×. Corretta la regola dei pulsanti pieni: uno per modulo (le Impostazioni hanno
  più moduli separati, ognuno col suo "Salva"), non uno per vista.
