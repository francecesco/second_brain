# Firmware — Tasti a click — Design / Spec

**Data:** 2026-10-11
**Stato:** implementato e **verificato sull'hardware il 2026-10-12** su USB (branch
`firmware-tasti-click`, firmware 0.8.3, 43 test host). Verificati: wake con USER dal deep
sleep senza modalità download (2026-10-11); click di PWR → registrazione, click dopo 8,9 s →
stop, 8046 ms di audio su 8099, "Salvato 0:08", upload `201`; triplo click → click in più
visto a 328 ms dall'avvio, registrazione scartata, DEV MODE, click di USER → riavvio; click di
USER → sync. **Deviazione** da §3.2–3.3: il secondo click di un triplo click spesso cade
durante l'avvio dell'app (~0,5 s dopo il click che sveglia) e non si vede, quindi qualunque
click in più nei primi `SB_DEV_CLICK_WINDOW_MS` (2 s dall'avvio) porta in DEV: anche il
doppio click rapido, che non è più uno stop. Non provato: l'accensione a batteria a scheda
spenta del tutto, dove PWR dovrebbe essere tenuto ~1 s (finché il firmware non aggancia
GPIO17); dal deep sleep il mantenimento è già attivo e basta il click.
**Sostituisce** i gesti di Fase 1a (`2026-09-23-firmware-capture-sync-design.md`): PWR tenuto =
registra, pressione breve di PWR = sync, USER entro 1 s dal boot = DEV.

## 1. Obiettivo

Tenere premuto il tasto per tutta la registrazione è scomodo. L'autore vuole:

- **tasto sotto (PWR)**: un click avvia la registrazione, un altro click la ferma, con gli
  stessi beep di oggi; **tre click** entrano in DEV MODE;
- **tasto sopra (USER)**: sveglia il device e sincronizza (upload, OTA, schermo).

## 2. Contesto e vincoli

- Tasti verificati sull'hardware il 2026-10-11: sotto = PWR (GPIO18), sopra = USER (GPIO0).
- PWR è l'unico che riaccende la scheda da spenta a batteria (mantenimento GPIO17): va bene,
  è il tasto della registrazione.
- Oggi solo PWR sveglia dal deep sleep (EXT1). USER va aggiunto alle sorgenti EXT1
  (`ESP_EXT1_WAKEUP_ANY_LOW`, GPIO0 è un pin RTC); il pin che ha svegliato si legge con
  `esp_sleep_get_ext1_wakeup_status()`.
- **Rischio**: GPIO0 è un pin di strapping. Basso all'accensione da zero porta il chip in
  modalità download; al risveglio dal deep sleep lo strapping non dovrebbe essere riletto,
  ma va verificato sull'hardware prima di tutto il resto (§6, punto 1).
- Dal click al beep di inizio oggi passano ~1,7 s (bootloader e init); la registrazione
  parte comunque prima del beep e il task registratore controlla già il tasto.
- Registrazioni sotto 1 s scartate, massimo 10 min: invariati.

## 3. Decisioni (approvate in chat il 2026-10-11)

1. **Click di PWR dal sonno (o accensione con PWR) = registra subito.** Non si aspetta di
   capire se è un triplo click: il beep di inizio resta veloce.
2. **Triplo click = DEV.** Se entro `SB_DEV_CLICK_WINDOW_MS` (1,5 s) dal risveglio arrivano
   in tutto tre click di PWR (il primo è quello che ha svegliato), la registrazione appena
   partita si scarta senza beep di fine né file, e il device entra in DEV MODE.
3. **Click di PWR durante la registrazione = stop**, con beep di fine e "Salvato" come oggi.
   Un secondo click dentro la finestra del triplo click (doppio click) ferma la
   registrazione come uno stop normale: dura meno di 1,5 s e di solito viene scartata.
4. **Click di USER dal sonno = sync** (oggi la pressione breve di PWR): Wi-Fi, upload, OTA,
   schermo aggiornato sempre.
5. **Uscita dalla DEV MODE con un click di USER** (oggi PWR), così il click in più di un
   triplo click non la chiude subito.
6. Il tasto USER durante la registrazione e PWR durante un sync non fanno nulla.
7. Accensione senza tasti (USB inserita, reset) = sync, come oggi; wake dal timer invariato.

## 4. Politica pura (`button_policy.c`, test host)

Il conteggio dei click è una funzione pura sul campionamento del tasto, così si prova
sull'host con sequenze di livelli:

```c
// Click di PWR riconosciuti in una sequenza di campioni (true = premuto) presi ogni
// SB_BUTTON_SAMPLE_MS dal risveglio: un click e' una pressione di almeno
// SB_BUTTON_DEBOUNCE_MS seguita da un rilascio (il primo puo' essere gia' in corso al boot).
int button_count_clicks(const bool *samples, int n);

// Gesto deciso alla fine della finestra del triplo click.
typedef enum { GESTURE_RECORD, GESTURE_STOP, GESTURE_DEV } wake_gesture_t;
wake_gesture_t button_wake_gesture(int clicks);   // 1 -> RECORD, 2 -> STOP, >=3 -> DEV
```

Il task registratore riusa la stessa logica di debounce per riconoscere il click di stop
(pressione dopo un rilascio) invece del rilascio di oggi.

## 5. Modifiche

- `power.c`: EXT1 su PWR e USER; `power_boot_mode()` decide dalla causa di wake e dal pin
  (PWR → `BOOT_CAPTURE`, USER → `BOOT_SYNC_ONLY`, timer → `BOOT_TIMER_SYNC`, nessun tasto →
  `BOOT_SYNC_ONLY`); sparisce la finestra di 1 s per USER al boot.
- `app_main.c` / registratore: la registrazione parte subito in `BOOT_CAPTURE`; durante la
  finestra di 1,5 s si contano i click; con tre si scarta e si va in DEV, altrimenti lo stop
  arriva al click successivo (o a 10 min).
- `dev_mode()`: si esce con un click di USER.
- `config.h`: `SB_DEV_CLICK_WINDOW_MS`, `SB_BUTTON_SAMPLE_MS`, `SB_BUTTON_DEBOUNCE_MS`.
- README e `CLAUDE.md`: nuovi gesti.

## 6. Verifica sull'hardware

1. **Prima di tutto**, con un firmware che aggiunge solo USER a EXT1: dal deep sleep un
   click di USER sveglia il device e parte l'app (nessuna modalità download), anche a
   batteria. Se fallisce, il design va rivisto prima di andare avanti.
2. Click di PWR dal sonno: beep di inizio, registrazione; dopo qualche secondo un click: beep
   di fine, "Salvato", upload.
3. Tre click rapidi di PWR: niente file in coda, schermata DEV MODE; click di USER: riavvio.
4. Click di USER dal sonno: sync, schermo aggiornato.
5. Due click rapidi di PWR: registrazione scartata ("Scartato"), nessun file.
6. Registrazione lasciata andare: stop automatico a 10 min (o con un massimo ridotto per la
   prova).
7. Test host verdi, build senza warning.
