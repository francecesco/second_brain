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
   pannelli chiari con bordo sottile e angoli di 2 px: intestazione, albero, contenuto.
   Dentro un pannello, le sezioni si separano con hairline, non con altri riquadri.
4. **Etichette in maiuscoletto, numeri in monospazio.** Tutto ciò che è etichetta
   (menu, briciole, intestazioni di tabella e di sezione, termini delle definizioni) è
   piccolo, maiuscolo e spaziato. Tutto ciò che è numero o identificativo (orari,
   contatori, dimensioni, id, hash, modelli) è in JetBrains Mono con cifre tabulari.
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
| `--panel` | `#f6f6f3` | `#232322` | pannelli, pulsanti, intestazione |
| `--fg` | `#1a1a1a` | `#ececea` | testo |
| `--mute` | `#74746f` | `#92928c` | testo secondario, etichette, orari, contatori |
| `--line` | `#c9c9c3` | `#3b3b38` | bordi e hairline |
| `--acc` | `#ff4d00` | `#ff5a14` | accento (vedi "Dove va l'arancione") |
| `--acc-fg` | `#fff` | `#fff` | testo sopra l'accento |
| `--acc-edge` | `#b53600` | `#9e3300` | bordo inferiore del pulsante principale |
| `--acc-bg` | `#ffe8dd` | `#3a2315` | fondo degli avvisi |
| `--mark-bg` | `#ffd3bd` | `#5a2a10` | evidenziazione dei risultati di ricerca |
| `--sans` | Helvetica Neue, Helvetica, Arial, system-ui | | testo |
| `--mono` | JetBrains Mono, ui-monospace, Menlo | | numeri e identificativi |

Il grotesk è quello di sistema (Helvetica su Mac e iPhone, Roboto su Android): non si
scarica nulla. JetBrains Mono è un font variabile (pesi 100–800, solo sottoinsieme
latino, 31 KB) in `web/static/jetbrains-mono.woff2`, licenza OFL nel file accanto. Se
servono altri glifi si rigenera il sottoinsieme, non si aggiunge un secondo file per peso.

## Tipografia

- Corpo: 14 px, interlinea 1,5.
- Etichetta (classe `.lab`, applicata anche a menu, briciole, `th`, `h2` delle card,
  `h3` dei campi AI, `dt` del dettaglio, tag): 10,5 px, maiuscolo, spaziatura 0,14 em,
  peso 600, colore `--mute`. Le `h2` delle card sono a 12 px e in `--fg`.
- Titolo di una riga: peso 500. Voce corrente dell'albero: peso 600 e `--acc`.
- Monospazio (`.mono`, `.time`, `.count`, `.meta`, `.provenance`, `td.mono`): dimensione
  del contesto o 0,85–0,9 em quando è secondario, sempre `font-variant-numeric:
  tabular-nums`.
- Niente titoli grandi: la gerarchia la fanno le etichette, gli spazi e le hairline.

## Dove va l'arancione

Sì, e solo qui:

- voce di menu attiva (classe `on`: testo e sottolineatura di 2 px);
- voce corrente dell'albero (classe `current`) e punto del marchio;
- orario della riga di cui è aperto il dettaglio (`.row:has(> .detail:not(:empty)) .time`);
- titolo di una riga o di un risultato al passaggio del mouse;
- pulsante principale (`button.pri`), uno per modulo: salva, entra;
- LED "in corso" (`.led.run`) e icona "fallita" (`.status.failed`);
- avvisi (`.notice`, `#flash`): bordo sinistro, icona e link;
- marcatore "corretto a mano" (`.edited`), badge "data stimata" (`.badge`), errori di
  modulo (`.error`), esito negativo di una prova (`.test-result.ko`);
- anello di focus da tastiera.

No:

- tag, briciole, intestazioni di sezione, contatori, bordi dei pannelli;
- link normali: sono in `--fg` e passano a `--acc` solo al passaggio del mouse;
- più di un pulsante pieno nella stessa vista.

## Componenti

**Pannelli e layout.** `header.top` (intestazione, bordo inferiore), `.layout` con
`aside.tree` (14 rem) e `main`, separati da 8 px di fondo grigio. Una pagina nuova
estende `finder.html` e riempie `{% block main %}`: eredita intestazione, albero e
briciole. Le briciole sono etichette con `›` tra una e l'altra.

**Pulsanti.** `<button>` ha già lo stile: fondo `--panel`, bordo 1 px, raggio 2 px,
ombra `0 2px 0 var(--line)` che fa da bordo inferiore, testo in etichetta. Al passaggio
il bordo diventa `--fg`, alla pressione il tasto scende di 1 px. `button.pri` è pieno
d'accento. I pulsanti con sola icona tengono l'etichetta in `<span class="label">`, che
sparisce sotto i 700 px. Dentro `.row-main` e `.top nav` i pulsanti sono "nudi" (niente
bordo né ombra): sono righe e voci di menu, non tasti.

**Campi.** `input`, `select`, `textarea`: fondo `--bg` (incassato rispetto al pannello),
bordo 1 px, raggio 2 px. Nelle card delle impostazioni, che hanno già fondo `--bg`, i
campi passano a `--panel`. Il focus è un anello di 2 px d'accento.

**Righe della lista** (`_row.html`). `.row` con hairline sotto; `.row-main` è un
`<button>` a tutta larghezza che apre il dettaglio via htmx in `.detail`. Ordine:
orario (`--mute`, monospazio), titolo, tag, LED di stato, meta a destra (dispositivo,
durata, dimensione in monospazio). La riga aperta si riconosce dall'orario arancione:
non servono classi aggiunte via JavaScript.

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
   `/static/` riscritti in percorsi `file://`, e due copie del CSS in cui il blocco
   `@media (prefers-color-scheme: dark)` diventa `@media all` (scuro) o `@media not all`
   (chiaro). Chrome headless su Mac segue il tema del sistema e non scende sotto ~500 px
   di finestra: per il telefono si mette la pagina in un `<iframe>` largo 390 px.
3. Guardare gli screenshot davvero: intestazione su una riga, niente scroll
   orizzontale, LED e orario arancione dove previsto, contrasto nel tema scuro.
4. `docker compose up -d --build` e controllo sul sito vero, ricaricando la pagina.
