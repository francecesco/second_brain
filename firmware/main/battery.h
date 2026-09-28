#pragma once
#include <stdbool.h>
#include "esp_err.h"
#include "battery_policy.h"

// Stato dell'alimentazione del ciclo corrente: sorgente stimata (USB/batteria), tensione e,
// solo a batteria, percentuale. Richiede sensors_init().
typedef struct {
    bool           valid;   // lettura ADC riuscita
    power_source_t source;
    int            mv;      // tensione misurata sul nodo VBAT
    int            pct;     // 0-100 se source == BATTERY, -1 su USB
} power_state_t;

// Legge e stima. remember=true salva la lettura come riferimento del ciclo successivo
// (una volta per ciclo); false per letture di sola consultazione (es. GET /status).
esp_err_t battery_read(power_state_t *out, bool remember);

// Ultimo stato letto con battery_read in questo ciclo.
const power_state_t *battery_last(void);

// true se a batteria e sotto la soglia pct (con USB o lettura fallita: false).
bool battery_below(int pct);

// Testo per il display: "USB" oppure "bat 78%" oppure "bat ?".
void battery_label(char *out, int n);
