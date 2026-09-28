#pragma once
#include <stdbool.h>
#include <stdint.h>

// Stima della sorgente di alimentazione e della carica (funzioni pure, testate su host).
// La scheda non ha un segnale di presenza USB: si deduce da tensione, andamento e
// collegamento dati USB. Soglie in config.h, da tarare con la batteria reale.
typedef enum { POWER_SRC_BATTERY, POWER_SRC_USB } power_source_t;

// Percentuale 0-100 da una curva di scarica LiPo tipica (a riposo, 1 cella).
int battery_pct_from_mv(int mv);

// mv/min_mv/max_mv: media, minimo e massimo di una raffica di campioni; prev_mv/dt_s:
// lettura del ciclo precedente e secondi trascorsi (prev_mv <= 0 se non disponibile);
// usb_host: la porta USB parla con un computer.
power_source_t battery_guess_source(int mv, int min_mv, int max_mv, int prev_mv, int32_t dt_s, bool usb_host);
