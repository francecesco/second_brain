#pragma once
#include <stdbool.h>
#include <stdint.h>
// Timer di sync (spec timer §4): quando riprovare, se armare il timer, se aggiornare il
// display dopo un wake da timer. Funzioni pure, testate su host.

// Ritardo del tentativo n (0 = primo): SB_SYNC_RETRY_MIN_MS raddoppiato, tetto SB_SYNC_RETRY_MAX_MS.
uint32_t sync_retry_delay_ms(unsigned attempt);

// Vero se va armato il timer: coda non vuota e, a batteria, percentuale non sotto la soglia
// di sync (SB_BATTERY_MIN_RECORD_PCT). A USB la percentuale non conta.
bool sync_retry_should_arm(int remaining, bool on_battery, int battery_pct);

// Vero se un wake da timer conta come tentativo fallito (il ritardo raddoppia): non ha
// inviato niente e lascia file in coda. Se ha fatto progressi si riprova al primo gradino.
bool sync_retry_counts_as_failure(int sent, int remaining);

// Esito del ciclo come lo mostra la riga 4 del display.
typedef enum {
    STATUS_OUTCOME_NONE,       // mai mostrato (memoria RTC azzerata)
    STATUS_OUTCOME_OK,         // "sync ok" / "Salvato"
    STATUS_OUTCOME_SENT,       // "N inviate"
    STATUS_OUTCOME_NO_WIFI,    // "no sync"
    STATUS_OUTCOME_SERVER_KO,  // "server ko"
    STATUS_OUTCOME_AUTH_KO,    // "token ko"
} status_outcome_t;

// Dopo un wake da timer: refresh se sono stati inviati file o l'esito e' cambiato.
bool status_should_refresh(int sent, status_outcome_t outcome, status_outcome_t shown);
