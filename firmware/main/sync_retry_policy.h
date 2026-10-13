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

// Ridisegno a un wake da timer: stato cambiato (status_should_refresh) oppure immagine
// diversa da quella mostrata. NULL vale "nessuna immagine".
bool today_should_refresh(bool status_changed, const char *etag_now, const char *etag_shown);
// Un file dell'immagine e' utilizzabile solo con la dimensione esatta.
bool digest_size_valid(long size);
// Un etag entra nella memoria RTC solo intero: troncato non combacerebbe piu' col server e
// l'immagine verrebbe ridisegnata a ogni wake.
bool digest_etag_storable(const char *etag);
// Se scaricare l'immagine "Oggi" in questo ciclo: serve rete e batteria sopra la soglia OTA, e
// il server non deve essere risultato irraggiungibile (connessione fallita all'OTA pull):
// riprovare costerebbe un altro paio di timeout di connessione.
bool digest_should_fetch(bool wifi_ok, bool battery_ok, bool server_unreachable);
