# Firmware — Timer di sync (wake senza tasto) — Design / Spec

**Data:** 2026-10-07
**Stato:** implementato e **verificato sull'hardware il 2026-10-07** via Wi-Fi (branch
`firmware-timer-sync`, piano `docs/plans/2026-10-07-firmware-timer-sync.md`, 35 test host).
Verificati: con backend fermo e un file in coda il device si è svegliato da solo a +15 min
(due volte, 14,5 e 15 min dalla registrazione) e ha caricato il file appena il backend è
tornato (`201`), con un solo refresh a `1 inviate`; a coda vuota nessuna richiesta per
35 min e `next_wake_s: 0`; con backend fermo per più tempo il primo wake fallito ha portato
`retry_attempt` a 1 e `next_wake_s` a 1800 con lo schermo immutato (`server ko`, `coda: 1`).
Non osservato il secondo raddoppio (3600) né lo scenario 4 di §8 (azzeramento dopo un wake
da tasto): coperti dal test host. Dalla revisione finale: il raddoppio scatta solo se il
wake da timer non ha inviato nulla (`sync_retry_counts_as_failure`), altrimenti si riparte
dal primo gradino. Deviazioni: nel ramo senza refresh il log stampa esito e coda come
numeri, non le quattro righe; emerso che il DHCP dell'ufficio ha cambiato l'IP del Mac durante la prova (`server ko` fino
alla build 0.7.7 con il nuovo `SERVER_BASE_URL`), un caso che il tunnel renderà irrilevante.

## 1. Obiettivo

Oggi il device si sveglia solo da PWR (EXT1). Una registrazione fatta senza rete, o con il
backend irraggiungibile, resta in `queue/` finché l'utente non preme di nuovo il tasto: può
passare un giorno. Il device deve riprovare da solo, senza consumare quando non c'è niente
da inviare e senza lampeggiare lo schermo a ogni tentativo.

## 2. Contesto e vincoli

- Deep sleep in `power_deep_sleep()` con wake EXT1 su `BOARD_BTN_PWR`; il wake da timer
  (`esp_sleep_enable_timer_wakeup`) si aggiunge senza toccare il tasto, e la causa si legge
  con `esp_sleep_get_wakeup_cause()` al boot.
- La memoria RTC (`RTC_DATA_ATTR`, già usata da `diag`) sopravvive al deep sleep ma non a
  un riavvio dopo OTA: i contatori vanno trattati come azzerabili.
- Un ciclo di solo sync costa circa 15 s sveglio (e-Paper, Wi-Fi, upload); senza batteria
  non c'è ancora una misura di consumo (vedi "Batteria" in `CLAUDE.md`).
- Soglie batteria esistenti: sotto il 10 % niente Wi-Fi (`SB_BATTERY_MIN_RECORD_PCT`),
  sotto il 30 % niente OTA; `battery_last()` è letto a inizio ciclo.
- `show_status` fa un refresh completo dell'e-Paper (~2 s di lampeggio nero).
- Scadenza di ciclo (5 min) e flusso in `app_main` restano come in Fase 1a §4.

## 3. Decisioni (approvate in chat)

1. **Timer solo con coda non vuota.** Al momento di dormire, se restano file in coda e la
   batteria non è sotto la soglia di sync, si arma anche il wake da timer. Coda vuota:
   nessun timer, nessun consumo in più. DEV MODE: nessun timer.
2. **Backoff.** Primo tentativo dopo 15 min, poi raddoppio a ogni wake da timer che non
   invia nulla e lascia la coda non vuota, fino a 4 h; il contatore si azzera a ogni wake
   da tasto, quando la coda si svuota e quando un wake da timer ha inviato almeno un file
   (arretrato lungo o finestra di sync esaurita: si riprova presto).
3. **Display solo se cambia qualcosa.** A un wake da timer lo schermo si aggiorna solo se è
   stato inviato almeno un file o se l'esito è diverso da quello mostrato l'ultima volta;
   altrimenti il device torna a dormire senza toccare l'e-Paper.
4. **Wake da timer = SYNC_ONLY senza tasti.** Nuova modalità `BOOT_TIMER_SYNC`: niente
   campionamento dei tasti (da timer non si entra in DEV né in CAPTURE), poi lo stesso
   flusso: sensori, SD, Wi-Fi, time sync, upload, OTA.

## 4. Politica pura (`sync_retry_policy.c`, test host)

```c
// Ritardo del prossimo tentativo per il tentativo n (0 = primo): 15 min, 30, 60, 120, 240,
// poi sempre 240 (SB_SYNC_RETRY_MIN_MS raddoppiato fino a SB_SYNC_RETRY_MAX_MS).
uint32_t sync_retry_delay_ms(unsigned attempt);

// Vero se va armato il timer: coda non vuota e batteria non sotto la soglia di sync
// (a USB la percentuale non conta).
bool sync_retry_should_arm(int remaining, bool on_battery, int battery_pct);

// Esito mostrato sul display (riga 4), per confrontare due cicli.
typedef enum { STATUS_OUTCOME_NONE, STATUS_OUTCOME_OK, STATUS_OUTCOME_SENT,
               STATUS_OUTCOME_NO_WIFI, STATUS_OUTCOME_SERVER_KO, STATUS_OUTCOME_AUTH_KO } status_outcome_t;

// Vero se il display va aggiornato dopo un wake da timer: file inviati, oppure esito
// diverso da quello mostrato. Ai wake da tasto si aggiorna sempre (non passa di qui).
bool status_should_refresh(int sent, status_outcome_t outcome, status_outcome_t shown);
```

Costanti in `config.h`: `SB_SYNC_RETRY_MIN_MS (15 * 60 * 1000)`, `SB_SYNC_RETRY_MAX_MS
(4 * 60 * 60 * 1000)`.

## 5. Modalità di boot e flusso

- `power_boot_mode()`: se `esp_sleep_get_wakeup_cause() == ESP_SLEEP_WAKEUP_TIMER` ritorna
  subito `BOOT_TIMER_SYNC` (nome `"TIMER"`), senza campionare i tasti. Gli altri casi
  invariati.
- `app_main`: `BOOT_TIMER_SYNC` segue il ramo di `BOOT_SYNC_ONLY` (nessuna cattura);
  `show_status` viene chiamata solo se `status_should_refresh(...)` è vero; il log stampa
  comunque le quattro righe.
- Stato in memoria RTC (`RTC_DATA_ATTR`, in `power.c` o modulo `retry_state`):
  `unsigned attempt` (tentativi consecutivi da timer) e `status_outcome_t shown` (ultimo
  esito mostrato). `attempt` torna a 0 su wake da tasto e quando la coda è vuota; `shown`
  si aggiorna a ogni refresh del display.
- `power_deep_sleep(uint32_t timer_ms)`: con `timer_ms > 0` abilita anche
  `esp_sleep_enable_timer_wakeup(timer_ms * 1000ULL)`; con `0` solo EXT1 (DEV, coda vuota,
  batteria bassa). Il log dice quando è il prossimo wake.
- La scadenza di ciclo (`deadline_cb`) chiama `power_deep_sleep(0)`: dopo un ciclo bloccato
  non si riprova da soli (il tasto resta l'unico wake), per non ripetere un blocco ogni
  15 min.

## 6. Display

Esito del ciclo → `status_outcome_t`: `auth_error` → `AUTH_KO`; `server_error` →
`SERVER_KO`; Wi-Fi assente → `NO_WIFI`; `sent > 0` → `SENT`; altrimenti `OK`. Al wake da
timer, refresh solo se `status_should_refresh`; il contenuto delle righe è quello di oggi
(ora, rete, coda + batteria, esito).

## 7. Diagnostica

`diag_t.mode` vale `"TIMER"` per i cicli da timer; `diag_t` acquista `uint32_t retry_attempt`
(tentativo corrente) e `uint32_t next_wake_s` (secondi al prossimo wake da timer, 0 se non
armato), esposti in `GET /status` → `last_cycle`.

## 8. Verifica

**Host**: `sync_retry_delay_ms` (0 → 15 min, 1 → 30, 4 → 240, 10 → 240),
`sync_retry_should_arm` (coda vuota → no; batteria 9 % → no; USB 0 % → sì; coda 1 e
batteria 50 % → sì), `status_should_refresh` (inviati → sì; stesso esito → no; esito
diverso → sì). Tutti i test esistenti verdi.

**Hardware** (USB, via Wi-Fi):

1. Backend fermo (`docker compose stop app`), una registrazione → `server ko`, `coda: 1`;
   `/status` in DEV non serve: il log del device non è leggibile, si osserva il backend.
   Dopo ~15 min, backend acceso: entro il wake successivo (al più 30 min) arriva il `201`
   e lo schermo passa a `1 inviate` (un solo refresh).
2. Coda vuota per un'ora: nessuna richiesta al backend (`docker compose logs`), `seq`
   invariato in `/status`.
3. Backend fermo per più di 45 min con un file in coda: `/status` mostra `retry_attempt`
   crescente e `next_wake_s` coerente col raddoppio; lo schermo non cambia (`server ko`
   resta).
4. Wake da tasto dopo i tentativi: `retry_attempt` torna a 0.

Alla fine si aggiorna lo "Stato" con data, numeri e deviazioni.

## 9. Non-goals

- Wake giornaliero a coda vuota per OTA e ora (si valuta con le misure di consumo).
- Light sleep, refresh parziale, misura del consumo per ciclo (batteria).

## 10. Rischi

- **Consumo a batteria**: ogni tentativo è ~15 s sveglio con Wi-Fi; con il tetto a 4 h un
  backend giù per un giorno costa 6-7 cicli in più. Da misurare con la batteria; le soglie
  sono in `config.h`.
- **Timer e tasto insieme**: se l'utente preme PWR mentre il timer è armato, il wake è da
  EXT1 e il timer viene semplicemente riarmato al prossimo sonno: nessun conflitto.
- **Memoria RTC dopo OTA**: `attempt` e `shown` possono azzerarsi; l'effetto è un refresh
  in più e un ritardo che riparte da 15 min.
