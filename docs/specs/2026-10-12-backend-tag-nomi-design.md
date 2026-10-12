# Backend — Tag per persone e programmi, vocabolario dei nomi — Design / Spec

**Data:** 2026-10-12
**Stato:** implementato sul branch `backend-tag-nomi` (560 test del backend), in prova
sul Mac. **Prove reali sul Mac (2026-10-12)** con l'audio della nota "Cloud/Soteri":
- trascrizione con **Groq Whisper**: "Cloud" anche con il vocabolario nel `prompt`, in tre
  formulazioni diverse (elenco, frase, "Nomi propri: …"): su questo audio il `prompt` non
  basta;
- trascrizione con **Gemini**: "Claude" giusto già senza vocabolario; l'ultima parola
  ("Soteri" per Groq) la omette, forse tagliata dallo stop della registrazione;
- arricchimento sul testo sbagliato: con il vocabolario Gemini corregge "cloud" in
  "claude", Groq no; "soteri" non lo recupera nessuno.
Quindi il rimedio più efficace per i nomi è trascrivere con Gemini (ordine dei provider
nelle Impostazioni), decisione lasciata all'autore. `retag --all` sulle 13 note del Mac:
fatto, con il passaggio automatico a Gemini quando Groq era al limite di token al minuto.

## 1. Obiettivo

L'autore vuole ritrovare tutte le note legate a una persona o a un programma (Home Assistant,
Claude, Costanza, Francesco…) cercando per tag. Oggi i tag sono "parole chiave" generiche
("saluto", "buongiorno", "domanda", "sera") e i nomi propri entrano solo per caso.

## 2. Cosa è emerso (dati sulla ZimaBoard, 2026-10-12)

- Il prompt di arricchimento chiede solo "da 1 a 8 parole chiave minuscole": nessuna regola
  sui nomi, nessun riuso dei tag esistenti.
- La trascrizione storpia i nomi: la nota che chiede questa funzione è stata trascritta
  "associate a Home Assistant, oppure associate a **Cloud**, oppure a **Soteri**" (Claude,
  Costanza), da cui il tag "soteri". Con nomi sbagliati nel testo nessun prompt può taggarli.

## 3. Decisioni (approvate in chat il 2026-10-12)

1. **Vocabolario dei nomi** nelle Impostazioni, curato dall'autore: persone, programmi,
   prodotti, luoghi, scritti come devono comparire. Usato:
   - nella **trascrizione**: parametro `prompt` di Whisper per Groq e OpenAI (il modo
     previsto per orientare la grafia dei nomi), testo delle istruzioni per Gemini;
   - nell'**arricchimento**: i nomi del vocabolario citati nella nota (anche se trascritti in
     modo simile, quando il contesto lo rende chiaro) diventano tag scritti così.
2. **Nuove regole dei tag** nel prompt di arricchimento: sempre tutte le persone e tutti i
   programmi, app, prodotti, servizi e dispositivi nominati, anche fuori vocabolario; poi
   da 1 a 3 temi; niente parole generiche, saluti, date; riuso dei tag già esistenti (i più
   usati) quando calzano. Massimo da 8 a **10** tag.
3. **Note esistenti: solo i tag.** Nuova fase di lavoro `retag` e comando
   `secondbrain retag --all` (o con un id): rifà l'arricchimento e cambia **solo i tag**;
   trascrizione, titolo e riassunto restano; i tag corretti a mano restano. Priorità bassa,
   salta le note già in coda o in lavorazione.

## 4. Scelte di dettaglio

- Tag sempre minuscoli come oggi (la ricerca per tag è esatta): "Home Assistant" nel
  vocabolario diventa il tag "home assistant".
- Vocabolario salvato come impostazione (`vocabulary`, lista di stringhe) nella tabella
  delle impostazioni; nella UI una textarea, un nome per riga; doppioni e righe vuote tolti;
  al massimo 200 nomi da 60 caratteri.
- Nel prompt di Whisper i nomi vanno come frase ("Nomi propri: A, B, C."), tagliata a
  ~800 caratteri (Whisper usa solo gli ultimi 224 token del prompt).
- Tag già usati passati all'arricchimento: i 40 più frequenti fuori dal cestino.
- Gli indizi (vocabolario, tag noti) li legge il worker a ogni lavoro: una modifica al
  vocabolario vale dalla nota successiva, senza riavvii.

## 5. Verifica

1. Test del backend: prompt con e senza vocabolario, parametro `prompt` di Whisper, istruzioni
   di Gemini, fase `retag` che cambia solo i tag e rispetta `edited`, comando `retag`,
   pagina Impostazioni (salvataggio e normalizzazione del vocabolario).
2. In locale sul Mac, con le chiavi vere: vocabolario compilato, una nota nuova dettata con
   i nomi, `secondbrain retag --all` sulle note esistenti; prova dell'autore dal suo PC.
3. Deploy sulla ZimaBoard solo dopo l'ok dell'autore, poi `retag --all` lì.
