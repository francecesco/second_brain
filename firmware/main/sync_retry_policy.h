#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "battery_policy.h"
// Wake periodico (spec wake periodico §4): se armare il timer e se aggiornare il display
// dopo un wake da timer. Funzioni pure, testate su host.

// Vero se va armato il timer di wake (SB_WAKE_PERIOD_MS): sempre, tranne a batteria sotto
// la soglia di sync (SB_BATTERY_MIN_RECORD_PCT). A USB la percentuale non conta.
bool wake_timer_should_arm(bool on_battery, int battery_pct);

// Esito del ciclo come lo mostra la riga 4 del display.
typedef enum {
    STATUS_OUTCOME_NONE,       // mai mostrato (memoria RTC azzerata)
    STATUS_OUTCOME_OK,         // "sync ok" / "Salvato"
    STATUS_OUTCOME_SENT,       // "N inviate"
    STATUS_OUTCOME_NO_WIFI,    // "no sync"
    STATUS_OUTCOME_SERVER_KO,  // "server ko"
    STATUS_OUTCOME_AUTH_KO,    // "token ko"
} status_outcome_t;

// Quello che la schermata di stato mostra, senza l'ora (che cambia a ogni wake).
typedef struct {
    status_outcome_t outcome;
    int              remaining;
    char             ssid[33];   // "" senza rete
    charge_state_t   charge;
    int              pct;        // -1 senza batteria
} status_view_t;

// Dopo un wake da timer: refresh se sono stati inviati file o se la vista e' cambiata; la
// percentuale conta solo se si sposta di almeno SB_BATTERY_DISPLAY_STEP_PCT punti.
bool status_should_refresh(int sent, const status_view_t *now, const status_view_t *shown);
