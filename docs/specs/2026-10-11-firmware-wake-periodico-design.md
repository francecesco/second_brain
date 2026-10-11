# Firmware — Wake periodico ogni 5 minuti — Design / Spec

**Data:** 2026-10-11
**Stato:** implementato e **verificato sull'hardware il 2026-10-11** via cavo (branch
`firmware-wake-periodico`, firmware 0.7.9, 36 test host). Verificati: su USB senza reti note
wake da solo a +298 s e +601 s (ciclo di ~3,8 s, `wake dal timer tra 300 s`), schermo non
aggiornato con la percentuale ferma all'86 % mentre la tensione saliva (4080 → 4091 mV);
staccata la USB lo schermo è passato da solo a "bat NN%"; con la schermata Hotspot personale
aperta il device si è collegato all'iPhone. Non osservati: scenario 4 di §6 (upload al wake,
serve il backend raggiungibile) e un refresh dovuto al solo scalino di 2 punti. Emerso:
l'hotspot dell'iPhone si vede solo con la schermata aperta o un client collegato (il Mac lo
vede comunque, ma via hotspot istantaneo), quindi a ogni wake di solito risulta "wifi:
assente".
**Sostituisce** le decisioni 1 e 2 di `2026-10-07-firmware-timer-sync-design.md` (timer solo
con coda non vuota, backoff 15 min → 4 h). La decisione 3 (display solo se cambia qualcosa)
viene estesa, la 4 (`BOOT_TIMER_SYNC` senza tasti) resta com'è.

## 1. Obiettivo

Con la batteria collegata il device deve restare aggiornato senza che l'utente prema PWR:
inviare le registrazioni in coda, prendere gli aggiornamenti OTA, mostrare sul display lo
stato vero (rete, coda, batteria e carica). Oggi a coda vuota si sveglia solo da tasto: lo
schermo resta, per esempio, su "in carica 84%" anche ore dopo aver staccato la USB.

## 2. Contesto e vincoli

- Il wake da timer e la modalità `BOOT_TIMER_SYNC` esistono già (spec del 2026-10-07): il
  ciclo fa sensori, SD, Wi-Fi, NTP (al massimo una volta al giorno), upload e OTA pull.
- Costo stimato di un ciclo, ancora da misurare: ~15 s svegli con backend raggiungibile,
  ~30 s con backend irraggiungibile (timeout OTA, oggi sull'hotspot), ~5 s senza reti note.
  Con un wake ogni 5 min l'autonomia stimata su 1000 mAh è di circa una settimana, quattro
  giorni nel caso peggiore, più il consumo in deep sleep non ancora misurato.
- La riga 1 del display ha l'ora: cambia a ogni wake, quindi non può decidere il refresh
  (2 s di lampeggio nero e consumo ogni 5 min).
- La percentuale oscilla di un punto tra due letture (campioni con ~12 mV di dispersione).

## 3. Decisioni (approvate in chat il 2026-10-11)

1. **Wake ogni 5 min sempre**, a USB e a batteria, con o senza file in coda e anche quando
   il ciclo precedente non è riuscito (niente backoff): `SB_WAKE_PERIOD_MS` in `config.h`.
2. **Eccezioni**: a batteria sotto `SB_BATTERY_MIN_RECORD_PCT` (10 %) niente timer, il
   device si sveglia solo da tasto (come oggi: niente Wi-Fi né registrazione). DEV MODE non
   dorme, quindi niente timer.
3. **Display solo se cambia qualcosa.** A un wake da timer lo schermo si aggiorna se sono
   stati inviati file, o se rispetto all'ultima schermata mostrata cambiano: esito della
   riga 4, numero di file in coda, rete, stato di carica (batteria / in carica / piena /
   USB), oppure la percentuale di almeno `SB_BATTERY_DISPLAY_STEP_PCT` (2) punti, per non
   lampeggiare a ogni oscillazione di un punto. L'ora della riga 1 diventa di fatto l'ora
   dell'ultimo aggiornamento.

## 4. Politica pura (`sync_retry_policy.c`, test host)

```c
// Vero se va armato il timer di wake: sempre, tranne a batteria sotto la soglia di sync.
bool wake_timer_should_arm(bool on_battery, int battery_pct);

// Quello che la schermata di stato mostra, senza l'ora.
typedef struct {
    status_outcome_t outcome;
    int              remaining;
    char             ssid[33];   // "" senza rete
    charge_state_t   charge;
    int              pct;        // -1 senza batteria
} status_view_t;

// Dopo un wake da timer: refresh se inviati file o se la vista e' cambiata (pct con soglia).
bool status_should_refresh(int sent, const status_view_t *now, const status_view_t *shown);
```

Spariscono `sync_retry_delay_ms` e `sync_retry_counts_as_failure` (con i loro test) e il
contatore dei tentativi in `retry_state` (`retry_attempt` esce da `/status`; `next_wake_s`
resta). `retry_state` conserva in memoria RTC l'ultima `status_view_t` mostrata; dopo un
power-on o un riavvio OTA è vuota e il primo wake aggiorna lo schermo.

## 5. Modifiche al flusso (`app_main.c`)

- `show_status` riceve la `status_view_t` e la salva come mostrata.
- A fine ciclo: refresh se non è un wake da timer o se `status_should_refresh`; timer armato
  con `SB_WAKE_PERIOD_MS` se `wake_timer_should_arm`, altrimenti 0 (solo tasto).
- Il resto del ciclo non cambia: soglie del 10 % (Wi-Fi) e del 30 % (OTA) incluse.

## 6. Verifica sull'hardware

1. Su USB, coda vuota: wake automatico a +5 min nel log (`next_wake_s: 300`), nessun refresh
   se non cambia nulla, refresh quando la percentuale di carica sale di 2 punti.
2. Staccata la USB: entro 5 min lo schermo passa da "in carica NN%" a "bat NN%" da solo.
3. Senza reti note: il device continua a svegliarsi ogni 5 min, schermo fermo su "no sync".
4. Con un file in coda e una rete nota col backend: upload al wake successivo e un refresh
   con "1 inviate".
5. Test host verdi, build senza warning.

## 7. Rimandato

- Misura del consumo reale per ciclo e in deep sleep, da cui dipende se 5 min sono
  sostenibili; se no, intervallo diverso a batteria o backoff sui fallimenti.
- Timeout più corti quando il backend non risponde (oggi ~21 s di OTA in timeout via hotspot).
