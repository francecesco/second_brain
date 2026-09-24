#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
// Upload della coda verso POST <server della rete corrente>/captures (spec §6.2-6.3).
typedef struct {
    int  sent;          // file accettati (200/201/409) e cancellati
    int  rejected;      // file spostati in rejected/ (altri 4xx)
    int  remaining;     // file ancora in coda alla fine
    bool server_error;  // sync interrotto per 5xx/timeout/rete
} sync_result_t;

// Presuppone Wi-Fi connesso e SD montata. Non inizia nuovi upload oltre window_ms.
esp_err_t sync_run(uint32_t window_ms, sync_result_t *out);
